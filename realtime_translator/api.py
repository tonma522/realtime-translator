"""Gemini API統合"""
import logging
import queue
import time
import threading
from dataclasses import dataclass
from datetime import datetime

from .constants import (
    GEMINI_MODEL,
    MIN_API_INTERVAL_SEC,
    API_QUEUE_MAXSIZE,
    SILENCE_SENTINEL,
    genai_types,
)
from .prompts import build_translation_prompt
from .worker_utils import enqueue_dropping_oldest, send_stop_sentinel

import re

# Disable thinking for gemini-2.5 models to get direct text output
_THINKING_CONFIG = None
if genai_types is not None:
    try:
        _THINKING_CONFIG = genai_types.GenerateContentConfig(
            thinking_config=genai_types.ThinkingConfig(thinkingBudget=0),
        )
    except (AttributeError, TypeError):
        logging.warning("ThinkingConfig not available in SDK; using defaults")
        _THINKING_CONFIG = None


def _generate_config_for_model(model: str):
    """2.5系モデルのみThinkingConfig無効化、それ以外はNone"""
    if "2.5" in model and _THINKING_CONFIG is not None:
        return _THINKING_CONFIG
    return None

_ERROR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"429|rate.?limit|exhausted", re.IGNORECASE),
     "APIレート制限に達しました。しばらくお待ちください。"),
    (re.compile(r"40[13]|unauthorized|forbidden|invalid.?api.?key", re.IGNORECASE),
     "APIキーが無効です。確認してください。"),
    (re.compile(r"500|internal.?server.?error", re.IGNORECASE),
     "Geminiサーバーエラーが発生しました。"),
    (re.compile(r"timeout|deadline", re.IGNORECASE),
     "API応答がタイムアウトしました。"),
]


def _localize_error(msg: str) -> str:
    """Map common Gemini API error messages to Japanese for UI display."""
    for pattern, ja_msg in _ERROR_PATTERNS:
        if pattern.search(msg):
            return ja_msg
    return msg


@dataclass
class ApiRequest:
    wav_bytes: bytes | None  # phase==2 では None
    prompt: str
    stream_id: str
    phase: int = 0        # 0=通常(STT+翻訳), 1=STTのみ, 2=翻訳のみ(テキスト入力)
    context: str = ""     # phase==2 へ引き継ぐコンテキスト
    transcript: str = ""  # phase==2 の入力テキスト


class ApiWorker:
    """シリアル処理+レート制限付きGemini APIワーカー"""

    def __init__(self, ui_queue: queue.Queue, client=None,
                 min_interval_sec: float = MIN_API_INTERVAL_SEC,
                 label: str = "ApiWorker",
                 model: str = GEMINI_MODEL) -> None:
        self._ui_queue = ui_queue
        self._client = client
        self._min_interval_sec = min_interval_sec
        self._label = label
        self._model = model
        # maxsize制限: キュー溢れ時は最古のリクエストを破棄しリアルタイム性を優先する（仕様）
        self._req_queue: queue.Queue[ApiRequest | None] = queue.Queue(maxsize=API_QUEUE_MAXSIZE)
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_call_time = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name=self._label, daemon=True)
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
            self._call_api(req)

    def _call_api(self, req: ApiRequest) -> None:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < self._min_interval_sec:
            time.sleep(self._min_interval_sec - elapsed)
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            if req.phase == 1:
                # Phase1: 音声→文字起こし（累積後 Phase2 へ）
                audio_part = genai_types.Part.from_bytes(data=req.wav_bytes, mime_type="audio/wav")
                chunks = []
                chunk_count = 0
                for chunk in self._client.models.generate_content_stream(
                    model=self._model, contents=[req.prompt, audio_part],
                    config=_generate_config_for_model(self._model),
                ):
                    chunk_count += 1
                    logging.debug("[%s] phase1 chunk #%d: candidates=%s",
                                  self._label, chunk_count, getattr(chunk, 'candidates', 'N/A'))
                    try:
                        t = chunk.text or ""
                    except ValueError:
                        logging.debug("[%s] phase1 chunk #%d: ValueError on .text", self._label, chunk_count)
                        continue
                    if t:
                        chunks.append(t)
                logging.debug("[%s] phase1 done: %d chunks, text=%r", self._label, chunk_count, "".join(chunks)[:200])
                transcript = "".join(chunks).strip()
                if transcript and SILENCE_SENTINEL not in transcript:
                    self._ui_queue.put(("transcript", req.stream_id, ts, transcript))
                    if self._running:
                        self.submit(ApiRequest(
                            wav_bytes=None,
                            prompt=build_translation_prompt(req.stream_id, req.context, transcript),
                            stream_id=req.stream_id, phase=2,
                            context=req.context, transcript=transcript,
                        ))
            else:
                # Phase0 (通常) または Phase2 (翻訳のみ): ストリーミング
                if req.wav_bytes is not None:
                    audio_part = genai_types.Part.from_bytes(data=req.wav_bytes, mime_type="audio/wav")
                    contents = [req.prompt, audio_part]
                else:
                    contents = [req.prompt]
                logging.debug("[%s] phase=%d stream=%s prompt=%.100s wav=%s",
                              self._label, req.phase, req.stream_id, req.prompt,
                              f"{len(req.wav_bytes)}B" if req.wav_bytes else "None")
                started = False
                chunk_count = 0
                try:
                    for chunk in self._client.models.generate_content_stream(
                        model=self._model, contents=contents,
                        config=_generate_config_for_model(self._model),
                    ):
                        chunk_count += 1
                        logging.debug("[%s] chunk #%d: candidates=%s",
                                      self._label, chunk_count,
                                      getattr(chunk, 'candidates', 'N/A'))
                        try:
                            text = chunk.text or ""
                        except ValueError:
                            logging.debug("[%s] chunk #%d: ValueError on .text", self._label, chunk_count)
                            continue
                        if not text:
                            logging.debug("[%s] chunk #%d: empty text", self._label, chunk_count)
                            continue
                        if not started:
                            self._ui_queue.put(("partial_start", req.stream_id, ts))
                            started = True
                        self._ui_queue.put(("partial", req.stream_id, text))
                except Exception as e:
                    logging.exception("[%s] streaming error", self._label)
                    self._ui_queue.put(("error", req.stream_id, _localize_error(str(e))))
                finally:
                    logging.debug("[%s] stream done: %d chunks, started=%s", self._label, chunk_count, started)
                    if started:
                        self._ui_queue.put(("partial_end", req.stream_id))
        except Exception as e:
            self._ui_queue.put(("error", req.stream_id, _localize_error(str(e))))
        finally:
            self._last_call_time = time.monotonic()
