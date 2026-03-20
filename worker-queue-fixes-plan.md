# Plan: Worker Queue & Counter Fixes — 6 Review Bugs

**Generated**: 2026-03-08
**Estimated Complexity**: Medium

## Overview
Fix 6 bugs identified in code review: `pending_requests` counter drift blocking idle detection, `send_stop_sentinel` silent failure, `enqueue_dropping_oldest` TOCTOU race, `PriorityQueue` + `None` sentinel crash in AssistWorker, `show_original` ignored in 2-phase transcript display, and `_seq_counter` global test leak. Each fix includes a regression test.

## Prerequisites
- Python 3.11+ (`pyproject.toml` の `requires-python = ">=3.11"` に準拠)
- `pytest` インストール済み
- 既存テスト一式を実行して基線を確定（`python -m pytest tests/ -q`）

---

## Sprint 1: Queue Infrastructure Fixes

**Goal**: Fix the foundational queue utilities that multiple workers depend on.

**Demo/Validation**:
- `python -m pytest tests/ -q` — all existing + new tests pass
- New test: concurrent enqueue stress test passes

### Task 1.1: Make `enqueue_dropping_oldest` atomic with a lock + return bool

- **Location**: `realtime_translator/worker_utils.py` L6-22
- **Description**: Add optional `threading.Lock` parameter. Wrap the `full() → get_nowait() → put_nowait()` sequence in the lock. Return `bool` indicating whether the item was enqueued.
- **Complexity**: 3
- **Dependencies**: None
- **Change**:
  ```python
  import threading  # add at top of file

  def enqueue_dropping_oldest(
      q: queue.Queue, item, label: str = "",
      lock: threading.Lock | None = None,
  ) -> bool:
      if lock is not None:
          lock.acquire()
      try:
          if q.full():
              try:
                  q.get_nowait()
                  if label:
                      logging.debug("[%s] queue full, dropped oldest request", label)
              except queue.Empty:
                  pass
          try:
              q.put_nowait(item)
              return True
          except queue.Full:
              return False
      finally:
          if lock is not None:
              lock.release()
  ```
- **Acceptance Criteria**:
  - When lock is provided: entire check-drop-put is atomic
  - When lock is None: no lock acquired (no false sense of safety)
  - Returns `True` on success, `False` on failure
  - `import threading` added at top of file

### Task 1.2: Fix `send_stop_sentinel` to guarantee delivery

- **Location**: `realtime_translator/worker_utils.py` L25-30
- **Description**: When queue is full, drop oldest to make room for `None` sentinel. Accept optional lock.
- **Complexity**: 2
- **Dependencies**: Task 1.1
- **Change**:
  ```python
  def send_stop_sentinel(q: queue.Queue, lock: threading.Lock | None = None) -> None:
      if lock is not None:
          lock.acquire()
      try:
          if q.full():
              try:
                  q.get_nowait()
              except queue.Empty:
                  pass
          try:
              q.put_nowait(None)
          except queue.Full:
              pass
      finally:
          if lock is not None:
              lock.release()
  ```

### Task 1.3: Add lock instances to all workers

- **Location**: `realtime_translator/api.py`, `realtime_translator/openai_llm.py`, `realtime_translator/whisper_stt.py`, `realtime_translator/openai_stt.py`, `realtime_translator/retranslation.py`
- **Description**: Add `self._queue_lock = threading.Lock()` to each worker `__init__`. Pass it to `send_stop_sentinel()` in `signal_stop()`. **Do NOT update `submit()` yet** — Sprint 2 will inline queue logic there. For `retranslation.py`, also apply the drop-oldest pattern in `signal_stop()` (same bug as the 4 main workers).
- **Complexity**: 2
- **Dependencies**: Task 1.1, 1.2
- **Changes per file**:
  - `api.py`: Add `self._queue_lock` after `self._req_queue` (~L82). Pass lock in `signal_stop()` (~L115). Leave `submit()` unchanged (Sprint 2).
  - `openai_llm.py`: Same pattern. `__init__` ~L84, `signal_stop()` ~L119.
  - `whisper_stt.py`: Same. `__init__` ~L43, `signal_stop()` ~L71.
  - `openai_stt.py`: Same. `__init__` ~L34, `signal_stop()` ~L63.
  - `retranslation.py`: Add `self._queue_lock` after `self._req_queue` (~L45). Update `signal_stop()` (~L72) to use `send_stop_sentinel(self._req_queue, self._queue_lock)`.
- **Note**: Line numbers are approximate — verify against current code.

### Task 1.4: Regression tests for queue utilities

- **Location**: `tests/test_worker_utils.py` (NEW)
- **Complexity**: 3
- **Dependencies**: Task 1.1, 1.2
- **Tests**:
  - `test_enqueue_returns_true_on_success` — empty queue → True
  - `test_enqueue_drops_oldest_when_full` — maxsize=2, fill, enqueue → oldest dropped, True
  - `test_enqueue_concurrent_safety` — 10 threads × 100 items, maxsize=3, shared lock → no exceptions, queue never exceeds maxsize
  - `test_enqueue_without_lock_no_crash` — call without lock → works (no lock acquired, no false safety)
  - `test_send_stop_sentinel_when_full` — fill to maxsize → None delivered
  - `test_send_stop_sentinel_when_empty` — empty queue → None delivered

---

## Sprint 2: `pending_requests` Counter Fix

**Goal**: Fix counter drift where `submit()` increments but `enqueue_dropping_oldest` drops the item without decrementing. This blocks `RetranslationWorker` and `AssistWorker` idle detection permanently.

**Demo/Validation**:
- `python -m pytest tests/ -q` — all tests pass
- New test: fill queue then submit extra → `pending_requests` stays bounded

### Task 2.1: Fix `pending_requests` in `ApiWorker.submit()`

- **Location**: `realtime_translator/api.py` L107-111
- **Description**: Inline the queue logic in `submit()` to correctly adjust the counter. When a full queue drops the oldest item, decrement for the dropped item. Only increment when `put_nowait` succeeds.
- **Complexity**: 3
- **Dependencies**: Task 1.3
- **Change**:
  ```python
  def submit(self, req: ApiRequest) -> None:
      if not self._running:
          return
      with self._queue_lock:
          if self._req_queue.full():
              try:
                  self._req_queue.get_nowait()
                  self._pending_requests = max(0, self._pending_requests - 1)
                  logging.debug("[%s] queue full, dropped oldest", self._label)
              except queue.Empty:
                  pass
          try:
              self._req_queue.put_nowait(req)
              self._pending_requests += 1
          except queue.Full:
              pass
  ```
- **Note**: This inlines instead of using `enqueue_dropping_oldest` because the counter adjustment is tightly coupled. The `signal_stop()` still uses `send_stop_sentinel` with the lock.

### Task 2.2: Fix `pending_requests` in `OpenAiLlmWorker.submit()`

- **Location**: `realtime_translator/openai_llm.py` L111-115
- **Description**: Same inline pattern as Task 2.1.
- **Complexity**: 2
- **Dependencies**: Task 2.1

### Task 2.3: Fix `pending_requests` in `WhisperWorker.submit()`

- **Location**: `realtime_translator/whisper_stt.py` L63-67
- **Description**: Same inline pattern as Task 2.1.
- **Complexity**: 2
- **Dependencies**: Task 2.1

### Task 2.4: Fix `pending_requests` in `OpenAiSttWorker.submit()`

- **Location**: `realtime_translator/openai_stt.py` L55-59
- **Description**: Same inline pattern as Task 2.1.
- **Complexity**: 2
- **Dependencies**: Task 2.1

### Task 2.5: Regression tests for `pending_requests` accuracy

- **Location**: `tests/test_api.py`, `tests/test_openai_llm.py`, `tests/test_whisper_stt.py`, `tests/test_openai_stt.py`
- **Complexity**: 4
- **Dependencies**: Task 2.1, 2.2, 2.3, 2.4
- **Tests** (add to all 4 test files):
  - `test_pending_requests_decrements_on_drop` — fill queue (maxsize=3), submit 4th → `pending_requests` == 3 (not 4)
  - `test_pending_requests_reaches_zero_after_processing` — submit items, let worker process all → `pending_requests` == 0
  - `test_pending_requests_never_negative` — process more items than submitted → stays 0
- **Additional regression tests for self-submit paths** (use `threading.Event` in mock client to control processing deterministically — avoid sleep-based assertions):
  - `tests/test_api.py`: `test_phase1_self_submit_with_full_queue` — `ApiWorker` Phase 1 transcript triggers Phase 2 self-submit when queue is full → `pending_requests` stays correct, Phase 2 enqueued (oldest dropped)
  - `tests/test_openai_llm.py`: `test_phase1_self_submit_with_full_queue` — `OpenAiLlmWorker._handle_phase1()` same scenario → `pending_requests` drift なし

---

## Sprint 3: AssistWorker PriorityQueue Sentinel Fix

**Goal**: Fix `TypeError` crash in `AssistWorker` `PriorityQueue` shutdown path, and make shutdown semantics explicit: after `signal_stop()`, the worker **drains already-enqueued requests until it dequeues `_STOP`**, then exits normally.

**Demo/Validation**:
- `python -m pytest tests/ -q` — all tests pass
- New test: submit 3 requests, then `signal_stop()` → all 3 processed, `_STOP` dequeued last, worker exits without `TypeError`

### Task 3.1: Replace `None` sentinel with `_StopSentinel` + drain-until-sentinel loop

- **Location**: `realtime_translator/assist.py`
- **Description**: Create `_StopSentinel` class that always sorts after any `AssistRequest`. Use in `signal_stop()` instead of `None`. **Change `_worker_loop` to drain-until-sentinel semantics**: the loop runs until `_STOP` is dequeued, not until `self._running` becomes `False`. `self._running = False` only prevents new submissions via `submit()` and skips idle-wait during shutdown.
- **Complexity**: 4
- **Dependencies**: None
- **Change**:
  ```python
  class _StopSentinel:
      """Sentinel that sorts after all real requests in PriorityQueue.
      heapq only uses __lt__, but we define all comparison ops for safety.
      """
      def __lt__(self, other): return False
      def __gt__(self, other): return not isinstance(other, _StopSentinel)
      def __le__(self, other): return isinstance(other, _StopSentinel)
      def __ge__(self, other): return True
      def __eq__(self, other): return isinstance(other, _StopSentinel)

  _STOP = _StopSentinel()
  ```
  `signal_stop()`: Use drop-oldest pattern + `_STOP`:
  ```python
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
  ```
  `_worker_loop()`: Change to drain-until-sentinel:
  ```python
  def _worker_loop(self) -> None:
      client = None
      while True:  # loop until _STOP, not while self._running
          try:
              req = self._req_queue.get(timeout=0.5)
          except queue.Empty:
              if not self._running:
                  break  # no more items and shutdown requested
              continue
          if isinstance(req, _StopSentinel):
              break
          # Skip idle-wait during shutdown (workers may already be stopped)
          if self._running:
              while self._running and not self._is_idle():
                  time.sleep(0.5)
          # ... rest of processing (rate limiting, LLM call, etc.)
  ```
- **Acceptance Criteria**:
  - `signal_stop()` with pending requests does not raise `TypeError`
  - `_STOP` sorts after all real `AssistRequest` items
  - If 3 requests are queued before `signal_stop()`, all 3 are processed before exit
  - Idle-wait is bypassed during shutdown to prevent hangs
  - Worker exits cleanly after dequeuing `_STOP`

### Task 3.2: Regression tests for PriorityQueue sentinel

- **Location**: `tests/test_assist.py`
- **Complexity**: 2
- **Dependencies**: Task 3.1
- **Tests**:
  - `test_signal_stop_drains_pending_requests_before_exit` — submit 3 requests, then `signal_stop()` → all 3 complete, `_STOP` consumed last, worker exits cleanly (no TypeError)
  - `test_stop_sentinel_sorts_after_requests` — put `_STOP` + `AssistRequest` in `PriorityQueue` → real request comes out first
  - `test_signal_stop_with_full_queue_on_retranslation` — `retranslation.py` の `signal_stop()` がキュー満杯でも sentinel を配信し、`join()` が戻ること（Task 1.3 の回帰テスト）

---

## Sprint 4: UI / Display / Test Isolation Fixes

**Goal**: Fix `show_original` ignored in 2-phase transcript, fix `_seq_counter` test leak.

**Demo/Validation**:
- `python -m pytest tests/ -q` — all tests pass
- Manual: 2-phase + `show_original=False` → transcript not shown

### Task 4.1: Respect `show_original` in `_on_transcript`

- **Location**: `realtime_translator/app.py` L617-622
- **Description**: When `show_original` is `False`, return early — skip Phase 1 transcript display. Phase 2 translation still displays via `_on_partial_*`. Skipping `_flush_active_partials()` is safe because `_on_partial_start` calls it before starting a new block.
- **Complexity**: 2
- **Dependencies**: None
- **Change**:
  ```python
  def _on_transcript(self, stream_id: str, ts: str, text: str) -> None:
      if not self._show_original_var.get():
          return
      self._flush_active_partials()
      label, tag, langs = _STREAM_META[stream_id]
      with self._editable_result():
          self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
          self._result_text.insert("end", f"原文: {text}\n", "original")
  ```

### Task 4.2: Reset `_seq_counter` in test fixtures

- **Location**: `tests/test_assist.py`
- **Description**: Add `autouse` fixture resetting `assist._seq_counter = 0`.
- **Complexity**: 1
- **Dependencies**: None

### Task 4.3: `_on_transcript` の `show_original` 回帰テスト

- **Location**: `tests/test_integration.py`
- **Complexity**: 3
- **Dependencies**: Task 4.1
- **Description**: 2つのレベルでテスト。(1) Controller レベル: `translation_done` イベントが `show_original` に関係なく `original` を含むことを検証。(2) UI レベル: `_on_transcript` メソッドを直接呼び出し、`show_original=False` 時に `insert()` が呼ばれないことをモックで検証。Tk の実画面初期化には依存しない。
- **Tests**:
  - `test_two_phase_translation_done_preserves_original_when_show_original_false` — controller-level: `translation_done` event contains original text regardless of `show_original`
  - `test_on_transcript_skips_insert_when_show_original_false` — UI-level: `TranslatorApp._on_transcript()` を `show_original=False` で呼んだ場合、`_result_text.insert()` が呼ばれない（`_show_original_var`, `_result_text`, `_stream_buffers` をモック）
  - `test_on_transcript_inserts_when_show_original_true` — UI-level: `show_original=True` 時は `insert()` が呼ばれる

---

## Testing Strategy
- `python -m pytest tests/ -q` after each Sprint
- Sprint 1: `tests/test_worker_utils.py` (new) + existing pass
- Sprint 2: `test_api.py`, `test_openai_llm.py`, `test_whisper_stt.py`, `test_openai_stt.py` — counter tests with `threading.Event` for deterministic control (no sleep-based assertions)
- Sprint 3: `test_assist.py` drain-until-sentinel tests + `test_retranslation.py` signal_stop tests
- Sprint 4: `test_assist.py` seq + `test_integration.py` show_original (controller + UI mock)

## Potential Risks & Gotchas
1. **Inlining queue logic in `submit()`**: Duplicates code across 4 workers. Acceptable — counter adjustment is tightly coupled to each worker's `_pending_requests`. A shared helper would need callbacks/mutable containers.
2. **Lock contention**: `submit()` called every 3-8s per stream. Lock acquisition <1μs. No impact.
3. **`_StopSentinel` comparison**: Must handle `_StopSentinel` vs `_StopSentinel` (two stops queued). `__eq__` and `__le__` return `True` for same type. `heapq` only uses `<` but all ops defined for safety.
4. **`_on_transcript` early return**: `_flush_active_partials()` skipped. Safe because `_on_partial_start` calls it. If Phase 2 fails (no `partial_start` after `transcript`), previously active partials remain unflushed until the next `partial_start`. This is pre-existing behavior.
5. **Phase 1→2 auto-submit from worker thread**: `api.py` Phase 1 calls `self.submit()` from worker thread. Since `submit()` now acquires `_queue_lock` and worker holds no other lock, no deadlock. The lock is per-worker-instance, not shared.
6. **`_pending_requests` decrement in worker thread**: `_worker_loop` decrements `_pending_requests` without `_queue_lock`. In CPython, the GIL makes individual attribute load/store atomic. The compound `max(0, self._pending_requests - 1)` could interleave with `submit()` under free-threaded Python 3.13+, but for this single-user desktop app on CPython this is acceptable. Documented as a known limitation.
7. **`send_stop_sentinel` drops oldest without decrementing counter**: During shutdown, the dropped item's counter stays inflated. Acceptable because shutdown doesn't check `pending_requests`.
8. **`enqueue_dropping_oldest` without lock**: When `lock=None`, no lock is acquired — provides no mutual exclusion. This is intentional (callers that need atomicity must pass a lock).
9. **Drain-until-sentinel + shutdown ordering**: `AssistWorker` has idle-wait loop. `controller.stop()` stops assist and monitored workers in the same phase. If monitored workers' `pending_requests`/`is_busy` don't reset properly, assist could block on idle-wait and never reach `_STOP`. **Mitigation**: skip idle-wait when `self._running` is `False` (implemented in Task 3.1).
10. **`previous_minutes` has no size limit** (out of scope): `AssistWorker._execute_minutes()` truncates history but not `previous_minutes`. Long meetings could exceed LLM token limits. Noted for future fix.
11. **Assist/retranslation concurrent API calls** (out of scope): `AssistWorker` and `RetranslationWorker` both make independent API calls. Neither monitors the other. Concurrent execution could trigger rate limits. Current design accepts this — both have `min_interval_sec=8.0`.

## Rollback Plan
- One commit per Sprint
- `git revert` any Sprint independently
- No config/data format changes

---
## Review Notes (Pre-Codex)

### Incorporated Feedback
- **Task 1.3 vs Task 2.1 overlap** [High]: Clarified that Task 1.3 only adds `_queue_lock` to `__init__` and `signal_stop()`. `submit()` is left unchanged — Sprint 2 replaces it entirely.
- **`RetranslationWorker.signal_stop()` same bug** [High]: Added `retranslation.py` to Task 1.3 scope.
- **`AssistWorker.signal_stop()` drop-oldest** [Medium]: Task 3.1 now includes drop-oldest logic.
- **False safety of fallback lock** [Medium]: Removed `lock or threading.Lock()` pattern.
- **Missing tests for WhisperWorker/OpenAiSttWorker** [Medium]: Task 2.5 now covers all 4 worker types.
- **Phase 1 self-submit stress test** [Medium]: Added to Task 2.5 for api.py.
- **`_StopSentinel.__eq__`** [Low]: Added `__eq__` for heapq edge cases.
- **`_pending_requests` worker-side race** [Low]: Documented as Risk item 6.
- **`import threading` in worker_utils.py** [Low]: Explicitly shown in code snippet.

### Skipped Feedback
- **Protecting `_pending_requests` decrement with lock**: CPython GIL sufficient. Documented as known limitation.

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **Sprint 3 drain-until-sentinel** [High]: `_worker_loop` を `while self._running` から drain-until-sentinel 方式に変更。停止要求後も既にキューにあるリクエストを処理してから終了する。idle-wait は shutdown 中はバイパス。
- **OpenAiLlmWorker self-submit テスト** [High]: Task 2.5 に `test_openai_llm.py` 用の `test_phase1_self_submit_with_full_queue` を追加。`ApiWorker` と同じ自己再投入パスの回帰テスト。
- **UI レベル show_original テスト** [High]: Task 4.3 を controller レベル + UI レベル（モック）の両方に拡張。`_on_transcript` の `insert()` 呼び出し有無を直接検証。
- **retranslation.py signal_stop 回帰テスト** [Medium]: Task 3.2 に `test_signal_stop_with_full_queue_on_retranslation` を追加。
- **Event ベーステスト推奨** [Medium]: Task 2.5 に「`threading.Event` でモッククライアントの処理開始/完了を制御し、sleep ベース assertions を回避」を明記。
- **Python 3.11 前提条件修正** [Low]: `pyproject.toml` の `requires-python = ">=3.11"` に合わせて修正。
- **既存テスト件数の断定回避** [Low]: 「315件パス」→「既存テスト一式を実行して基線を確定」に変更。
- **shutdown 中の idle-wait ハング** (Step 3 hidden risk): Risk item 9 として追加。drain-until-sentinel 方式で `_running=False` 時に idle-wait をスキップする設計。
- **`previous_minutes` 無制限投入** (Step 3 hidden risk): Risk item 10 として追加（本計画のスコープ外）。
- **assist/retranslation 同時 API 呼び出し** (Step 3 hidden risk): Risk item 11 として追加（本計画のスコープ外）。

### Skipped Feedback
- **Task 1.1 の `return bool` 未使用**: Sprint 2 ではインライン化するため `enqueue_dropping_oldest` の戻り値は直接使われないが、API の一貫性として残す。将来のコードや外部利用者が活用可能。変更コストは極小。
- **Task 1.4 並行テストの TOCTOU 検出力**: `queue.Queue` 自体が maxsize を保証するため、lock なしで壊れるテストを書くのは非決定的で flaky になりやすい。lock あり/なしの結果整合性テストより、lock 付きの正常動作テストに注力。
- **`request_type` 入力バリデーション** (Step 3 hidden risk): 現状は controller 経由でのみ呼ばれ、typo リスクは低い。バリデーション追加は本バグ修正計画のスコープ外。
- **AssistWorker/RetranslationWorker のエラーメッセージ日本語化** (Step 3 hidden risk): UX 改善だがバグ修正ではない。別タスクで対応。
