"""Gemini API統合"""
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
from .worker_utils import enqueue_dropping_oldest, stop_worker_thread


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
                 label: str = "ApiWorker") -> None:
        self._ui_queue = ui_queue
        self._client = client
        self._min_interval_sec = min_interval_sec
        self._label = label
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

    def stop(self) -> None:
        self._running = False
        self._thread = stop_worker_thread(self._req_queue, self._thread, timeout=10)

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
                for chunk in self._client.models.generate_content_stream(
                    model=GEMINI_MODEL, contents=[req.prompt, audio_part]
                ):
                    try:
                        t = chunk.text or ""
                    except ValueError:
                        continue
                    if t:
                        chunks.append(t)
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
                started = False
                try:
                    for chunk in self._client.models.generate_content_stream(
                        model=GEMINI_MODEL, contents=contents
                    ):
                        try:
                            text = chunk.text or ""
                        except ValueError:
                            continue
                        if not text:
                            continue
                        if not started:
                            self._ui_queue.put(("partial_start", req.stream_id, ts))
                            started = True
                        self._ui_queue.put(("partial", req.stream_id, text))
                except Exception as e:
                    self._ui_queue.put(("error", req.stream_id, str(e)))
                finally:
                    if started:
                        self._ui_queue.put(("partial_end", req.stream_id))
        except Exception as e:
            self._ui_queue.put(("error", req.stream_id, str(e)))
        finally:
            self._last_call_time = time.monotonic()
