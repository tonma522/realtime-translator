"""OpenAI Whisper STT ワーカー (OpenAI / OpenRouter共用)"""
import io
import logging
import queue
import threading
from datetime import datetime

from .api import ApiRequest, ApiWorker
from .constants import OPENAI_STT_DEFAULT_MODEL
from .openai_llm import _localize_openai_error
from .prompts import build_translation_prompt
from .worker_utils import enqueue_dropping_oldest, send_stop_sentinel


class OpenAiSttWorker:
    """OpenAI Whisper API → ApiWorker/OpenAiLlmWorker(Phase2翻訳) パイプライン"""

    def __init__(
        self,
        api_worker_listen,
        api_worker_speak,
        ui_queue: queue.Queue,
        client=None,
        model: str = OPENAI_STT_DEFAULT_MODEL,
        language: str | None = None,
        context: str = "",
    ) -> None:
        self._api_workers = {"listen": api_worker_listen, "speak": api_worker_speak}
        self._ui_queue = ui_queue
        self._client = client
        self._model = model
        self._language = language
        self._context = context
        self._req_queue: queue.Queue = queue.Queue(maxsize=3)
        self._running = False
        self._thread: threading.Thread | None = None
        self._pending_requests = 0
        self._is_busy = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name="OpenAiSttWorker", daemon=True,
        )
        self._thread.start()

    @property
    def pending_requests(self) -> int:
        return self._pending_requests

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    def submit(self, wav_bytes: bytes, stream_id: str) -> None:
        if not self._running:
            return
        self._pending_requests += 1
        enqueue_dropping_oldest(self._req_queue, (wav_bytes, stream_id), "OpenAiSttWorker")

    def signal_stop(self) -> None:
        self._running = False
        send_stop_sentinel(self._req_queue)

    def join(self, timeout: float = 15) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stop(self) -> None:
        self.signal_stop()
        self.join(timeout=15)

    def _worker_loop(self) -> None:
        self._ui_queue.put(("status", "OpenAI STT準備完了"))

        while self._running:
            try:
                item = self._req_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            wav_bytes, stream_id = item
            ts = datetime.now().strftime("%H:%M:%S")
            self._is_busy = True
            try:
                transcript = self._transcribe(wav_bytes)
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
                self._ui_queue.put(("error", stream_id, _localize_openai_error(e)))
            finally:
                self._is_busy = False
                self._pending_requests = max(0, self._pending_requests - 1)

    def _transcribe(self, wav_bytes: bytes) -> str:
        """OpenAI Whisper APIで文字起こし"""
        kwargs: dict = {"model": self._model, "file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")}
        if self._language:
            kwargs["language"] = self._language
        logging.debug("[OpenAiSttWorker] transcribe model=%s lang=%s bytes=%d",
                      self._model, self._language, len(wav_bytes))
        response = self._client.audio.transcriptions.create(**kwargs)
        return response.text
