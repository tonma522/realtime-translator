"""Whisper STT (ローカル文字起こし)"""
import io
import logging
import queue
import threading
from datetime import datetime

from .constants import WHISPER_AVAILABLE, WhisperModel
from .api import ApiRequest, ApiWorker
from .prompts import build_translation_prompt


class WhisperTranscriber:
    """faster-whisper ローカル文字起こし"""

    def __init__(self, model_size: str = "small", language: str | None = None):
        if not WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper が未インストールです (pip install faster-whisper)")
        # device="cpu" を明示（Windows CUDA DLL競合を回避）
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._language = language

    def transcribe(self, wav_bytes: bytes) -> str:
        segments, _ = self._model.transcribe(
            io.BytesIO(wav_bytes),
            language=self._language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments)


class WhisperWorker:
    """Whisper STT → ApiWorker(Phase2翻訳) パイプライン"""

    def __init__(self, api_worker_listen: ApiWorker, api_worker_speak: ApiWorker,
                 ui_queue: queue.Queue, model_size: str, language: str | None, context: str):
        self._api_workers = {"listen": api_worker_listen, "speak": api_worker_speak}
        self._ui_queue = ui_queue
        self._model_size = model_size
        self._language = language
        self._context = context
        self._req_queue: queue.Queue = queue.Queue(maxsize=3)
        self._running = False
        self._thread: threading.Thread | None = None
        self._transcriber: WhisperTranscriber | None = None  # スレッド内で初期化

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="WhisperWorker", daemon=True)
        self._thread.start()

    def submit(self, wav_bytes: bytes, stream_id: str) -> None:
        if not self._running:
            return
        if self._req_queue.full():
            try:
                self._req_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self._req_queue.put_nowait((wav_bytes, stream_id))
        except queue.Full:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            self._req_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=15)
            self._thread = None

    def _worker_loop(self) -> None:
        self._ui_queue.put(("status", "Whisper準備中..."))
        try:
            self._transcriber = WhisperTranscriber(self._model_size, self._language)
            self._ui_queue.put(("status", "Whisper準備完了"))
        except Exception as e:
            self._ui_queue.put(("error", "whisper", str(e)))
            self._running = False
            return

        while self._running:
            try:
                item = self._req_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            wav_bytes, stream_id = item
            ts = datetime.now().strftime("%H:%M:%S")
            try:
                transcript = self._transcriber.transcribe(wav_bytes)
                if transcript and transcript.strip():
                    self._ui_queue.put(("transcript", stream_id, ts, transcript))
                    worker = self._api_workers.get(stream_id)
                    if worker and worker.is_running:
                        worker.submit(ApiRequest(
                            wav_bytes=None,
                            prompt=build_translation_prompt(stream_id, self._context, transcript),
                            stream_id=stream_id, phase=2,
                            context=self._context, transcript=transcript,
                        ))
            except Exception as e:
                self._ui_queue.put(("error", stream_id, f"Whisper: {e}"))
