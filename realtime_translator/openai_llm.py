"""OpenAI互換LLMワーカー (OpenAI直接 / OpenRouter共用)"""
import logging
import queue
import time
import threading
from datetime import datetime

from .api import ApiRequest
from .audio_utils import wav_to_base64
from .constants import (
    API_QUEUE_MAXSIZE,
    MIN_API_INTERVAL_SEC,
    SILENCE_SENTINEL,
)
from .prompts import build_translation_prompt
from .worker_utils import enqueue_dropping_oldest, send_stop_sentinel

# phase=0/1 で音声入力が使えるモデル (既知)
AUDIO_CAPABLE_MODELS: set[str] = {
    # OpenAI
    "gpt-4o-audio-preview",
    "gpt-4o-audio-preview-2024-12-17",
    "gpt-4o-mini-audio-preview",
    "gpt-4o-mini-audio-preview-2024-12-17",
    # OpenRouter (provider prefix付き)
    "google/gemini-2.0-flash-001",
    "google/gemini-2.0-flash-lite-001",
    "google/gemini-2.5-flash",
}


def _localize_openai_error(exc: Exception) -> str:
    """OpenAI SDK例外を日本語UIメッセージに変換（型ベース判定）"""
    try:
        from openai import (
            RateLimitError,
            AuthenticationError,
            APITimeoutError,
            InternalServerError,
        )
    except ImportError:
        return str(exc)

    if isinstance(exc, RateLimitError):
        return "APIレート制限に達しました。しばらくお待ちください。"
    if isinstance(exc, AuthenticationError):
        return "APIキーが無効です。確認してください。"
    if isinstance(exc, APITimeoutError):
        return "API応答がタイムアウトしました。"
    if isinstance(exc, InternalServerError):
        return "APIサーバーエラーが発生しました。"
    return str(exc)


def _build_messages(prompt: str, wav_bytes: bytes | None) -> list[dict]:
    """ApiRequest.prompt をOpenAI messagesリストに変換"""
    content: list[dict] = [{"type": "text", "text": prompt}]
    if wav_bytes is not None:
        content.append({
            "type": "input_audio",
            "input_audio": {"data": wav_to_base64(wav_bytes), "format": "wav"},
        })
    return [{"role": "user", "content": content}]


class OpenAiLlmWorker:
    """OpenAI Chat Completions互換ワーカー (OpenAI / OpenRouter共用)"""

    def __init__(
        self,
        ui_queue: queue.Queue,
        client=None,
        min_interval_sec: float = MIN_API_INTERVAL_SEC,
        label: str = "OpenAiLlmWorker",
        model: str = "gpt-4o",
    ) -> None:
        self._ui_queue = ui_queue
        self._client = client
        self._min_interval_sec = min_interval_sec
        self._label = label
        self._model = model
        self._req_queue: queue.Queue[ApiRequest | None] = queue.Queue(
            maxsize=API_QUEUE_MAXSIZE
        )
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_call_time = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name=self._label, daemon=True
        )
        self._thread.start()

    def submit(self, req: ApiRequest) -> None:
        if not self._running:
            return
        enqueue_dropping_oldest(self._req_queue, req, self._label)

    def signal_stop(self) -> None:
        self._running = False
        send_stop_sentinel(self._req_queue)

    def join(self, timeout: float = 10) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stop(self) -> None:
        self.signal_stop()
        self.join(timeout=10)

    def _worker_loop(self) -> None:
        while self._running:
            try:
                req = self._req_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if req is None:
                break
            if not self._running:
                break
            self._call_api(req)

    def _call_api(self, req: ApiRequest) -> None:
        if not self._running:
            return
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < self._min_interval_sec:
            time.sleep(self._min_interval_sec - elapsed)
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            if req.phase == 1:
                self._handle_phase1(req, ts)
            else:
                self._handle_phase0_2(req, ts)
        except Exception as exc:
            self._ui_queue.put(("error", req.stream_id, _localize_openai_error(exc)))
        finally:
            self._last_call_time = time.monotonic()

    def _handle_phase1(self, req: ApiRequest, ts: str) -> None:
        """Phase1: 音声→文字起こし → phase=2自動投入"""
        if req.wav_bytes is not None and not self._is_audio_capable():
            self._ui_queue.put((
                "error", req.stream_id,
                f"モデル {self._model} は音声入力に対応していません。",
            ))
            return

        messages = _build_messages(req.prompt, req.wav_bytes)
        logging.debug("[%s] phase1 stream=%s model=%s", self._label, req.stream_id, self._model)

        chunks: list[str] = []
        for chunk in self._client.chat.completions.create(
            model=self._model, messages=messages, stream=True,
        ):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = delta.content if delta and delta.content else ""
            if text:
                chunks.append(text)

        transcript = "".join(chunks).strip()
        logging.debug("[%s] phase1 done: text=%r", self._label, transcript[:200])

        if transcript and SILENCE_SENTINEL not in transcript:
            self._ui_queue.put(("transcript", req.stream_id, ts, transcript))
            if self._running:
                self.submit(ApiRequest(
                    wav_bytes=None,
                    prompt=build_translation_prompt(
                        req.stream_id, req.context, transcript
                    ),
                    stream_id=req.stream_id,
                    phase=2,
                    context=req.context,
                    transcript=transcript,
                ))

    def _handle_phase0_2(self, req: ApiRequest, ts: str) -> None:
        """Phase0 (STT+翻訳) / Phase2 (翻訳のみ): ストリーミング"""
        if req.wav_bytes is not None and not self._is_audio_capable():
            self._ui_queue.put((
                "error", req.stream_id,
                f"モデル {self._model} は音声入力に対応していません。",
            ))
            return

        messages = _build_messages(req.prompt, req.wav_bytes)
        logging.debug(
            "[%s] phase=%d stream=%s model=%s wav=%s",
            self._label, req.phase, req.stream_id, self._model,
            f"{len(req.wav_bytes)}B" if req.wav_bytes else "None",
        )

        started = False
        chunk_count = 0
        try:
            for chunk in self._client.chat.completions.create(
                model=self._model, messages=messages, stream=True,
            ):
                chunk_count += 1
                if not chunk.choices:
                    logging.debug("[%s] chunk #%d: empty choices", self._label, chunk_count)
                    continue
                delta = chunk.choices[0].delta
                text = delta.content if delta and delta.content else ""
                if not text:
                    continue
                if not started:
                    self._ui_queue.put(("partial_start", req.stream_id, ts))
                    started = True
                self._ui_queue.put(("partial", req.stream_id, text))
        except Exception as exc:
            logging.exception("[%s] streaming error", self._label)
            self._ui_queue.put(("error", req.stream_id, _localize_openai_error(exc)))
        finally:
            logging.debug("[%s] stream done: %d chunks, started=%s", self._label, chunk_count, started)
            if started:
                self._ui_queue.put(("partial_end", req.stream_id))

    def _is_audio_capable(self) -> bool:
        """現在のモデルが音声入力に対応しているか"""
        return self._model in AUDIO_CAPABLE_MODELS
