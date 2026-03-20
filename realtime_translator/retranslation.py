"""再翻訳ワーカー"""
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .constants import STREAM_LANGS
from .history import TranslationHistory
from .prompts import build_retranslation_prompt
from .stream_modes import STREAM_DIRECTION_LANGS, split_stream_id
from .translation_postprocess import annotate_translation
from .worker_utils import send_stop_sentinel


@dataclass
class RetranslationRequest:
    batch_id: str
    center_seq: int
    n_surrounding: int
    context: str


class RetranslationWorker:
    """再翻訳ワーカー — 全ワーカーがアイドルのときのみ処理"""

    def __init__(
        self,
        ui_queue: queue.Queue,
        history: TranslationHistory,
        workers: list[Any],
        llm_backend: str,
        model: str,
        api_key: str,
        min_interval_sec: float = 8.0,
        client_factory=None,
    ) -> None:
        self._ui_queue = ui_queue
        self._history = history
        self._workers = workers
        self._llm_backend = llm_backend
        self._model = model
        self._api_key = api_key
        self._min_interval_sec = min_interval_sec
        self._client_factory = client_factory
        self._req_queue: queue.Queue[RetranslationRequest | None] = queue.Queue(maxsize=20)
        self._queue_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_call_time = 0.0

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name="RetranslationWorker", daemon=True,
        )
        self._thread.start()

    def submit(self, center_seq: int, n_surrounding: int, context: str) -> str:
        batch_id = uuid.uuid4().hex[:8]
        req = RetranslationRequest(
            batch_id=batch_id,
            center_seq=center_seq,
            n_surrounding=n_surrounding,
            context=context,
        )
        try:
            self._req_queue.put_nowait(req)
        except queue.Full:
            self._ui_queue.put(("retrans_error", batch_id, "再翻訳キューが満杯です"))
        return batch_id

    def signal_stop(self) -> None:
        self._running = False
        send_stop_sentinel(self._req_queue, self._queue_lock)

    def join(self, timeout: float = 10) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stop(self) -> None:
        self.signal_stop()
        self.join(timeout=10)

    def _all_workers_idle(self) -> bool:
        return all(
            w.pending_requests == 0 and not w.is_busy
            for w in self._workers
        )

    def _worker_loop(self) -> None:
        client = None
        while self._running:
            try:
                req = self._req_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if req is None:
                break

            # Wait for all workers to be idle
            while self._running and not self._all_workers_idle():
                time.sleep(0.5)
            if not self._running:
                break

            # Rate limiting
            elapsed = time.monotonic() - self._last_call_time
            if elapsed < self._min_interval_sec:
                time.sleep(self._min_interval_sec - elapsed)

            try:
                # Lazy client creation
                if client is None and self._client_factory:
                    client = self._client_factory()

                result = self._execute_retranslation(req, client)
                self._ui_queue.put(("retrans_result", req.batch_id, req.center_seq, result))
            except Exception as e:
                logging.exception("[RetranslationWorker] error")
                self._ui_queue.put(("retrans_error", req.batch_id, str(e)))
            finally:
                self._last_call_time = time.monotonic()

    def _execute_retranslation(self, req: RetranslationRequest, client) -> str:
        entry = self._history.get_by_seq(req.center_seq)
        if entry is None:
            raise ValueError(f"履歴エントリ #{req.center_seq} が見つかりません")
        if not entry.usable_for_downstream:
            raise ValueError(entry.error or "再翻訳対象外の履歴エントリです")

        entries = self._history.get_range(req.center_seq, req.n_surrounding, req.n_surrounding)
        history_block = self._format_history_block(entries, req.center_seq)
        prompt = build_retranslation_prompt(self._resolve_prompt_stream_id(entry), req.context, history_block)

        if self._llm_backend == "gemini":
            result = self._call_gemini(client, prompt)
        else:
            result = self._call_openai(client, prompt)

        try:
            return annotate_translation(
                result,
                output_language=self._resolve_output_language(entry),
            )
        except Exception:
            logging.exception("retranslation annotation failed; keeping raw translation")
            return result

    def _format_history_block(self, entries: list, center_seq: int) -> str:
        lines = []
        for e in entries:
            if not e.usable_for_downstream:
                continue
            src, dst = self._resolve_direction_labels(e)
            direction = f"{src}→{dst}"
            marker = ">>>" if e.seq == center_seq else "   "
            lines.append(f"{marker} [{direction}] {e.original} → {e.translation}")
        return "\n".join(lines)

    def _resolve_prompt_stream_id(self, entry) -> str:
        if entry.resolved_direction in STREAM_DIRECTION_LANGS:
            return f"{entry.stream_id}_{entry.resolved_direction}"
        if entry.virtual_stream_id:
            return entry.virtual_stream_id
        return entry.stream_id

    def _resolve_direction_labels(self, entry) -> tuple[str, str]:
        if entry.resolved_direction in STREAM_DIRECTION_LANGS:
            return STREAM_DIRECTION_LANGS[entry.resolved_direction]
        if entry.virtual_stream_id in STREAM_LANGS:
            return STREAM_LANGS[entry.virtual_stream_id]
        return STREAM_LANGS.get(entry.stream_id, ("?", "?"))

    def _resolve_output_language(self, entry) -> str:
        if entry.resolved_direction == "en_ja":
            return "ja"
        if entry.resolved_direction == "ja_en":
            return "en"
        stream_id = self._resolve_prompt_stream_id(entry)
        source_stream_id, mode = split_stream_id(stream_id)
        if mode == "auto":
            _, mode = split_stream_id(source_stream_id)
        return "ja" if mode == "en_ja" else "en"

    def _call_gemini(self, client, prompt: str) -> str:
        from .api import _generate_config_for_model
        response = client.models.generate_content(
            model=self._model, contents=[prompt],
            config=_generate_config_for_model(self._model),
        )
        return (response.text or "").strip()

    def _call_openai(self, client, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        response = client.chat.completions.create(
            model=self._model, messages=messages, stream=False,
        )
        if response.choices:
            return (response.choices[0].message.content or "").strip()
        return ""
