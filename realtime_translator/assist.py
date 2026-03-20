"""返答アシスト・議事録生成ワーカー"""
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .constants import STREAM_LANGS
from .history import TranslationHistory
from .prompts import build_reply_assist_prompt, build_minutes_prompt
from .stream_modes import STREAM_DIRECTION_LANGS

MAX_HISTORY_ENTRIES = 200
MAX_HISTORY_CHARS = 50_000

_seq_counter = 0
_seq_lock = threading.Lock()


def _next_seq() -> int:
    global _seq_counter
    with _seq_lock:
        _seq_counter += 1
        return _seq_counter


class _StopSentinel:
    """Sentinel that sorts after all real requests in PriorityQueue."""
    def __lt__(self, other): return False
    def __gt__(self, other): return not isinstance(other, _StopSentinel)
    def __le__(self, other): return isinstance(other, _StopSentinel)
    def __ge__(self, other): return True
    def __eq__(self, other): return isinstance(other, _StopSentinel)

_STOP = _StopSentinel()


def build_history_for_assist(entries: list) -> list:
    return [entry for entry in entries if entry.usable_for_downstream]


@dataclass(order=False)
class AssistRequest:
    request_id: str
    request_type: str  # "reply_assist" | "minutes"
    context: str
    n_history: int = 20
    previous_minutes: str = ""
    priority: int = 0  # 0=reply_assist (high), 1=minutes (low)
    seq: int = field(default_factory=_next_seq)

    def __lt__(self, other) -> bool:
        if isinstance(other, _StopSentinel):
            return True  # real requests sort before _STOP
        return (self.priority, self.seq) < (other.priority, other.seq)


class AssistWorker:
    """返答アシスト・議事録ワーカー — 全ワーカーがアイドルのときのみ処理"""

    def __init__(
        self,
        ui_queue: queue.Queue,
        history: TranslationHistory,
        monitored_workers: list[Any],
        llm_backend: str,
        model: str,
        api_key: str,
        min_interval_sec: float = 8.0,
        client_factory=None,
    ) -> None:
        self._ui_queue = ui_queue
        self._history = history
        self._monitored_workers = monitored_workers
        self._llm_backend = llm_backend
        self._model = model
        self._api_key = api_key
        self._min_interval_sec = min_interval_sec
        self._client_factory = client_factory
        self._req_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=20)
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_call_time = 0.0

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name="AssistWorker", daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        request_type: str,
        context: str,
        n_history: int = 20,
        previous_minutes: str = "",
    ) -> str:
        request_id = uuid.uuid4().hex[:8]
        priority = 0 if request_type == "reply_assist" else 1
        req = AssistRequest(
            request_id=request_id,
            request_type=request_type,
            context=context,
            n_history=n_history,
            previous_minutes=previous_minutes,
            priority=priority,
        )
        try:
            self._req_queue.put_nowait(req)
        except queue.Full:
            self._ui_queue.put(("assist_error", request_id, request_type, "アシストキューが満杯です"))
        return request_id

    def signal_stop(self) -> None:
        self._running = False
        try:
            self._req_queue.put_nowait(_STOP)
        except queue.Full:
            try:
                self._req_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._req_queue.put_nowait(_STOP)
            except queue.Full:
                pass

    def join(self, timeout: float = 10) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stop(self) -> None:
        self.signal_stop()
        self.join(timeout=10)

    def _is_idle(self) -> bool:
        """全 monitored_workers が idle かチェック"""
        return all(
            w.pending_requests == 0 and not w.is_busy
            for w in self._monitored_workers
        )

    def _worker_loop(self) -> None:
        client = None
        while True:
            try:
                req = self._req_queue.get(timeout=0.5)
            except queue.Empty:
                if not self._running:
                    break
                continue
            if isinstance(req, _StopSentinel):
                break

            # Wait for all workers to be idle (skip during shutdown)
            if self._running:
                while self._running and not self._is_idle():
                    time.sleep(0.5)

            # Rate limiting
            elapsed = time.monotonic() - self._last_call_time
            if elapsed < self._min_interval_sec:
                time.sleep(self._min_interval_sec - elapsed)

            try:
                if client is None and self._client_factory:
                    client = self._client_factory()

                entries = build_history_for_assist(self._history.all_entries())
                if not entries:
                    self._ui_queue.put((
                        "assist_error", req.request_id, req.request_type,
                        "翻訳履歴が空です",
                    ))
                    continue

                if req.request_type == "reply_assist":
                    result = self._execute_reply_assist(req, client, entries)
                else:
                    result = self._execute_minutes(req, client, entries)

                if not result or not result.strip():
                    self._ui_queue.put((
                        "assist_error", req.request_id, req.request_type,
                        "LLMから空の応答が返されました",
                    ))
                else:
                    self._ui_queue.put((
                        "assist_result", req.request_id, req.request_type, result,
                    ))
            except Exception as e:
                logging.exception("[AssistWorker] error")
                self._ui_queue.put((
                    "assist_error", req.request_id, req.request_type, str(e),
                ))
            finally:
                self._last_call_time = time.monotonic()

    def _execute_reply_assist(self, req: AssistRequest, client, entries: list) -> str:
        recent = entries[-req.n_history:]
        history_block = self._format_history_for_assist(recent)
        prompt = build_reply_assist_prompt(req.context, history_block)
        return self._call_llm(client, prompt)

    def _execute_minutes(self, req: AssistRequest, client, entries: list) -> str:
        truncated = self._truncate_history(entries)
        history_block = self._format_history_for_minutes(truncated)
        prompt = build_minutes_prompt(req.context, history_block, req.previous_minutes)
        return self._call_llm(client, prompt)

    @staticmethod
    def _truncate_history(entries: list) -> list:
        """履歴を MAX_HISTORY_ENTRIES / MAX_HISTORY_CHARS で切り詰め"""
        if len(entries) > MAX_HISTORY_ENTRIES:
            entries = entries[-MAX_HISTORY_ENTRIES:]
        total = 0
        start_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            e = entries[i]
            total += len(e.original) + len(e.translation) + 30  # overhead
            if total > MAX_HISTORY_CHARS:
                start_idx = i + 1
                break
        return entries[start_idx:]

    @staticmethod
    def _format_history_for_assist(entries: list) -> str:
        lines = []
        for e in entries:
            src, dst = AssistWorker._resolve_direction_labels(e)
            direction = f"{src}→{dst}"
            lines.append(AssistWorker._format_history_line(direction, e))
        return "\n".join(lines)

    @staticmethod
    def _format_history_for_minutes(entries: list) -> str:
        lines = []
        for e in entries:
            src, dst = AssistWorker._resolve_direction_labels(e)
            direction = f"{src}→{dst}"
            lines.append(AssistWorker._format_history_line(direction, e, include_timestamp=e.timestamp))
        return "\n".join(lines)

    @staticmethod
    def _resolve_direction_labels(entry) -> tuple[str, str]:
        if entry.resolved_direction in STREAM_DIRECTION_LANGS:
            return STREAM_DIRECTION_LANGS[entry.resolved_direction]
        if entry.virtual_stream_id in STREAM_LANGS:
            return STREAM_LANGS[entry.virtual_stream_id]
        return STREAM_LANGS.get(entry.stream_id, ("?", "?"))

    @staticmethod
    def _format_history_line(direction: str, entry, include_timestamp: str | None = None) -> str:
        prefix = f"[{include_timestamp}] " if include_timestamp else ""
        return f"{prefix}[{direction}] {entry.original} → {entry.translation}"

    def _call_llm(self, client, prompt: str) -> str:
        if self._llm_backend == "gemini":
            return self._call_gemini(client, prompt)
        else:
            return self._call_openai(client, prompt)

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
