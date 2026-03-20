# Plan: _record_loop Strategy Pattern + TranslatorController Extraction

**Generated**: 2026-03-08
**Estimated Complexity**: High

## Overview

Two coupled refactors that improve testability and separation of concerns:

1. **Task 4.2 — _record_loop strategy pattern**: Extract the three recording modes (PTT, VAD, Continuous) from the monolithic `_record_loop` method into pluggable strategy classes. Each strategy implements `process_frame(data) -> Optional[bytes]`. AudioCapture keeps its current constructor API and builds the strategy internally.

2. **Task 4.6 — TranslatorController extraction**: Split `TranslatorApp` (607 lines) into a UI-free `TranslatorController` (orchestration/business logic) and a thin `TranslatorApp` (tkinter UI only). Controller uses factory injection for worker creation. Full test coverage (~25-30 tests).

**Dependency**: Sprint 1 (strategies) is independent. Sprint 2 (controller) is independent. Sprint 3 (integration) depends on both.

## Prerequisites
- All existing pytest suite passes
- Current `signal_stop()`/`join()` refactor is committed
- No external library changes needed

---

## Sprint 1: Recording Strategy Pattern

**Goal**: Extract per-frame processing logic from `_record_loop` into three strategy classes. The main loop becomes a thin dispatcher.

**Demo/Validation**:
- `python -m pytest tests/ -q` — all tests pass
- `_record_loop` reduced from ~100 lines to ~30 lines
- Each strategy is independently unit-testable

### Task 1.1: Define RecordStrategy protocol

- **Location**: `realtime_translator/record_strategies.py` (new file)
- **Description**: Create a `RecordStrategy` Protocol class with two methods:
  - `process_frame(data: bytes) -> bytes | None` — process one audio frame, return WAV bytes when a complete chunk is ready, else None
  - `flush() -> bytes | None` — API to retrieve any buffered audio. Defined for future use but NOT called during normal or error stop in Sprint 1 (preserves current behavior where partial audio is discarded on stop).
- **Complexity**: 2
- **Dependencies**: None
- **Acceptance Criteria**:
  - Protocol defined with proper type hints
  - Importable from `realtime_translator.record_strategies`
- **Validation**:
  - Module imports cleanly

### Task 1.2: Implement ContinuousStrategy

- **Location**: `realtime_translator/record_strategies.py`
- **Description**: Extract continuous mode logic (current `_record_loop` lines 128-140) into `ContinuousStrategy`:
  - `__init__(frames_needed: int, channels: int, sample_rate: int, silence_threshold: int)`
  - Accumulates frames in internal list, tracks `total_frames`
  - `process_frame()` returns WAV bytes when `total_frames >= frames_needed` and audio is not silent, else None
  - Resets accumulator after emission
  - Uses `AudioCapture._to_wav()` (extract as module-level `frames_to_wav()`) and `is_silent_pcm()`
- **Complexity**: 3
- **Dependencies**: Task 1.1, Task 1.5 (`frames_to_wav`)
- **Acceptance Criteria**:
  - Equivalent behavior to current continuous mode
  - Silent chunks filtered (returns None)
  - Accumulator resets after emission
- **Validation**:
  - Unit tests: silent input returns None, loud input returns WAV bytes after frames_needed, accumulator resets

### Task 1.3: Implement PTTStrategy

- **Location**: `realtime_translator/record_strategies.py`
- **Description**: Extract PTT mode logic (current `_record_loop` lines 87-101) into `PTTStrategy`:
  - `__init__(ptt_event: threading.Event, channels: int, sample_rate: int, silence_threshold: int)`
  - Tracks `was_ptt_active` state
  - `process_frame()` accumulates while PTT active, emits WAV on PTT release (if not silent)
  - `flush()` returns any buffered frames (API defined but not called on stop in Sprint 1)
- **Complexity**: 3
- **Dependencies**: Task 1.1, Task 1.5 (`frames_to_wav`)
- **Acceptance Criteria**:
  - Records only while PTT event is set
  - Emits on transition from active→inactive
  - Silent recordings filtered
- **Validation**:
  - Unit tests: PTT press→release cycle emits WAV, silent PTT returns None, flush emits buffered data

### Task 1.4: Implement VADStrategy

- **Location**: `realtime_translator/record_strategies.py`
- **Description**: Extract VAD mode logic (current `_record_loop` lines 102-127) into `VADStrategy`:
  - `__init__(vad: VoiceActivityDetector, sample_rate: int, channels: int, chunk_seconds: int, silence_threshold: int)`
  - Manages `speech_frames`, `silent_count`, `silence_trigger`, `max_speech_chunks`
  - `process_frame()` uses VAD to detect speech, accumulates, emits on silence or max length
  - **Key subtlety**: `silent_count` only increments when `speech_frames` is non-empty (silence without prior speech is ignored). Max-length check (`len(speech_frames) >= max_speech_chunks`) runs on EVERY frame regardless of speech/silence — preserve this exact behavior.
  - `flush()` returns any buffered speech frames (API defined but not called on stop in Sprint 1)
- **Complexity**: 4
- **Dependencies**: Task 1.1, Task 1.5 (`frames_to_wav`)
- **Acceptance Criteria**:
  - Speech detection triggers accumulation
  - Silence after speech triggers emission
  - Max speech length triggers forced emission
  - Equivalent thresholds to current code
- **Validation**:
  - Unit tests: speech→silence emits, max length emits, pure silence returns None

### Task 1.5: Extract `frames_to_wav()` as module-level function

- **Location**: `realtime_translator/audio_utils.py`
- **Description**: Move `AudioCapture._to_wav()` static method to `audio_utils.py` as `frames_to_wav(frames, channels, sample_rate) -> bytes`. Keep `AudioCapture._to_wav` as a thin delegate for backward compatibility (one line: `return frames_to_wav(...)`). Import in `record_strategies.py`.
- **Complexity**: 2
- **Dependencies**: None (can be done in parallel with Task 1.1)
- **Acceptance Criteria**:
  - `frames_to_wav()` produces identical output to `AudioCapture._to_wav()`
  - `AudioCapture._to_wav` still works (backward compat)
- **Validation**:
  - Existing tests pass unchanged
  - New unit test comparing outputs

### Task 1.6: Refactor `_record_loop` to use strategies

- **Location**: `realtime_translator/audio.py`
- **Description**:
  - Add `_build_strategy()` method that selects strategy based on constructor params:
    - `ptt_event is not None` → `PTTStrategy`
    - `use_vad and ptt_event is None` → `VADStrategy`
    - else → `ContinuousStrategy`
  - Simplify `_record_loop`: after PyAudio setup, call `_build_strategy(sample_rate, channels)`, then main loop becomes:
    ```python
    while self._running:
        data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
        wav_bytes = strategy.process_frame(data)
        if wav_bytes:
            self._safe_callback(wav_bytes)
    # No flush on stop: preserve current behavior exactly.
    # strategy.flush() exists as API but is not called here in Sprint 1.
    ```
  - Extract callback exception handling into `_safe_callback(wav_bytes)` helper
  - **Error handling split**: `stream.read()` errors are fatal (break). Strategy/callback errors are logged and continue (via `_safe_callback`). Neither normal stop nor error stop calls `strategy.flush()`.
  - Keep the outer try/except for stream errors and error_callback unchanged
  - Add optional `strategy` parameter to `AudioCapture.__init__` (default `None` = auto-build). When provided, auto-build is bypassed completely and mode params (`ptt_event`, `use_vad`, `silence_threshold`) are ignored. This is a low-cost escape hatch for testing AudioCapture with mock strategies without requiring PyAudio (though `pa` must still be faked for stream setup).
- **Complexity**: 5
- **Dependencies**: Tasks 1.1-1.5
- **Acceptance Criteria**:
  - `_record_loop` body is ~30 lines (down from ~100)
  - All three modes work identically to before
  - No behavioral changes
  - Constructor API unchanged
- **Validation**:
  - All existing pytest suite passes
  - Manual: verify PTT, VAD, continuous modes still work (if devices available)

### Task 1.7: Unit tests for strategies

- **Location**: `tests/test_record_strategies.py` (new file)
- **Description**: Comprehensive tests for all three strategies:
  - `TestContinuousStrategy`: accumulation, emission on threshold, silence filtering, reset
  - `TestPTTStrategy`: press/release cycle, silence filter, flush on stop, no emission while held
  - `TestVADStrategy`: speech detection, silence trigger, max length, flush
  - `TestFramesToWav`: output format validation
  - All tests use synthetic PCM data (reuse `_make_silent_pcm`/`_make_sine_pcm` from test_audio.py)
- **Complexity**: 4
- **Dependencies**: Tasks 1.1-1.5
- **Acceptance Criteria**:
  - ~15-20 test cases
  - No PyAudio or real audio devices needed
  - 100% branch coverage of strategy logic
- **Validation**:
  - `python -m pytest tests/test_record_strategies.py -v` — all pass

---

## Sprint 2: TranslatorController Extraction

**Goal**: Extract business/orchestration logic from `TranslatorApp` into a UI-free `TranslatorController` class that is fully unit-testable via factory injection.

**Demo/Validation**:
- `python -m pytest tests/ -q` — all tests pass
- `TranslatorController` has zero tkinter imports
- ~25-30 new tests covering orchestration logic

### Task 2.1: Define controller interface and factory types

- **Location**: `realtime_translator/controller.py` (new file)
- **Description**: Define the `TranslatorController` class skeleton with:
  - Type aliases for factories:
    ```python
    AudioCaptureFactory = Callable[..., AudioCapture]
    ApiWorkerFactory = Callable[..., ApiWorker]
    WhisperWorkerFactory = Callable[..., WhisperWorker]
    GenaiClientFactory = Callable[[str], Any]  # api_key -> client
    ```
  - Constructor signature:
    ```python
    def __init__(self, ui_queue: queue.Queue,
                 capture_factory: AudioCaptureFactory = AudioCapture,
                 api_worker_factory: ApiWorkerFactory = ApiWorker,
                 whisper_worker_factory: WhisperWorkerFactory = WhisperWorker,
                 client_factory: GenaiClientFactory | None = None):
    ```
  - Callback interface for UI updates (errors, status):
    ```python
    on_error: Callable[[str], None] | None = None
    on_status: Callable[[str], None] | None = None
    ```
  - State: `_running`, `_ptt_event`, worker references, `_stream_buffers`
- **Complexity**: 3
- **Dependencies**: None
- **Acceptance Criteria**:
  - Clean separation: no tkinter imports
  - Factory defaults = real classes (production use requires no extra setup)
  - Callbacks optional (default: log only)
- **Validation**:
  - Module imports cleanly, no tkinter dependency

### Task 2.2: Move `_start_inner()` logic to controller and centralize pre-start validation

- **Location**: `realtime_translator/controller.py`, `realtime_translator/app.py`
- **Description**: Create `TranslatorController.start(config: StartConfig) -> None` and make the controller the single owner of start-up precondition validation:
  - Define `StartConfig` dataclass:
    ```python
    @dataclass
    class StartConfig:
        api_key: str
        context: str
        chunk_seconds: int
        enable_listen: bool
        enable_speak: bool
        loopback_device_index: int | None
        mic_device_index: int | None
        ptt_enabled: bool
        use_vad: bool
        request_whisper: bool
        request_two_phase: bool
        whisper_model: str = "small"
        whisper_language: str | None = None
    ```
  - Move ALL start-time validation from `app.py` into `TranslatorController.start()`:
    - `GENAI_AVAILABLE` check
    - API key presence and format validation (**warning-only**: format mismatch calls `on_error` but does NOT raise — preserves non-blocking behavior from commit 35ae25b)
    - Stream enablement check (at least one stream enabled)
    - Device index validity checks
    - `WHISPER_AVAILABLE` check when `request_whisper=True`
    - Normalization of mutually dependent options (`use_whisper`, `use_two_phase`, `ptt_enabled`, `use_vad`)
  - `app.py` must only collect raw UI state into `StartConfig` and call `controller.start()`; it must NOT independently re-check `GENAI_AVAILABLE` or `WHISPER_AVAILABLE`
  - Controller computes effective runtime flags from `StartConfig` and then performs worker instantiation / `AudioCapture` creation using factories
  - Validation failures raise `ValueError` with user-facing Japanese message; API key format warning is non-blocking (does not raise)
- **Complexity**: 5
- **Dependencies**: Task 2.1
- **Acceptance Criteria**:
  - 開始前バリデーションの責務は controller の 1 箇所に固定されている
  - `google-genai` 未導入時は `ValueError("google-genai が未インストールです。")` を送出
  - Whisper 要求時に未導入なら `ValueError` を送出、未要求時は開始を妨げない
  - API key format warning は `on_error` callback で警告を表示するが起動は継続する（非 fatal）
  - `app.py` には `GENAI_AVAILABLE` / `WHISPER_AVAILABLE` による開始可否判定が残っていない
- **Validation**:
  - `GENAI_AVAILABLE=False` の単体テスト
  - `request_whisper=True` + `WHISPER_AVAILABLE=False` の単体テスト
  - API key format warning が non-blocking であることのテスト

### Task 2.3: Move `_stop()` logic to controller

- **Location**: `realtime_translator/controller.py`
- **Description**: Create `TranslatorController.stop() -> None`:
  - Two-phase shutdown pattern preserved (signal all → join all)
  - PTT event cleared
  - UI queue drained
  - Stream buffers cleared
  - `_running` set to False
- **Complexity**: 3
- **Dependencies**: Task 2.1
- **Acceptance Criteria**:
  - Identical shutdown behavior to current `_stop()`
  - Shutdown ordering: `signal_stop all → join all → drain queue → clear buffers → set _running=False` (matches current `_stop()` ordering where `_running=False` is set AFTER joins)
  - No tkinter code (no button/keyboard unbinding — that stays in app)
- **Validation**:
  - Unit tests verifying signal_stop/join called on all workers
  - Test that `_running` is `False` after `stop()` completes

### Task 2.4: Move `_on_audio_chunk()` and `toggle()` to controller

- **Location**: `realtime_translator/controller.py`
- **Description**:
  - Controller stores `_context`, `_use_two_phase`, `_use_whisper` as instance state from `start()`. This avoids leaking business config into app-level closures.
  - `on_audio_chunk(wav_bytes: bytes, stream_id: str)` — simplified signature (2 params, not 4). Controller internally routes based on stored state:
    - If `_use_whisper`: submit to `_whisper_worker.submit(wav_bytes, stream_id)`
    - If `_use_two_phase`: submit `ApiRequest(phase=1)` to correct ApiWorker
    - Else: submit `ApiRequest(phase=0)` to correct ApiWorker
  - Audio callbacks from `start()` become simple lambdas: `lambda wav: self.on_audio_chunk(wav, stream_id)`
  - `toggle(config: StartConfig)` — start if not running, stop if running
  - `ptt_press()` / `ptt_release()` — set/clear PTT event. Guarded by `can_ptt` property (returns `True` only when running AND speak capture is active). Controller does NOT set PTT event when `can_ptt` is False.
  - Properties: `is_running -> bool`, `can_ptt -> bool` (True when `_running` and `_capture_speak` is not None)
- **Complexity**: 3
- **Dependencies**: Tasks 2.2, 2.3
- **Acceptance Criteria**:
  - Audio chunks dispatched to correct worker
  - Toggle delegates to start/stop
  - PTT events managed
- **Validation**:
  - Unit tests for dispatch routing

### Task 2.5: Refactor `TranslatorApp` to delegate to controller

- **Location**: `realtime_translator/app.py`
- **Description**:
  - Create `TranslatorController` in `__init__` with real factories and callbacks wired to UI methods
  - `_start_inner()` → collect UI values into `StartConfig`, call `self._controller.start(config)`
  - `_start()` remains the app-level exception boundary and preserves current UI contract:
    - `ValueError` from `controller.start()` → user-facing validation error only; show via `_append_error(str(e))`, do NOT emit `logging.exception()`
    - Unexpected exceptions → caught in `_start()`, reported with `logging.exception()` AND shown via `_append_error(f"起動エラー: {e}")`
  - On start failure, keep pre-start UI state unchanged: `_running` stays `False`, button stays `▶ 翻訳開始`, status not advanced
  - `_stop()` → call `self._controller.stop()`, then do UI cleanup (unbind keys, reset PTT button, update button/status)
  - `_toggle()` → delegate, update UI based on `_controller.is_running`
  - `_on_audio_chunk()` → delegate to controller
  - `_ptt_press()` / `_ptt_release()` → delegate + update button visuals only when `controller.can_ptt` permits
  - `_poll_queue()` stays in app (it does UI updates)
  - Keep config save/load in app (they read/write UI variables)
  - Keep device management in app (combo box population)
- **Complexity**: 5
- **Dependencies**: Tasks 2.1-2.4
- **Acceptance Criteria**:
  - `TranslatorApp` has zero worker instantiation logic
  - All business logic delegated to controller
  - UI-only code remains in app
  - Start failure behavior matches current contract:
    - validation failures shown as Japanese UI errors without traceback logging
    - unexpected failures produce `logging.exception()` plus Japanese UI error
    - failed start does not leave button/status/running state in partially started state
  - Existing behavior unchanged
- **Validation**:
  - All existing pytest suite passes
  - Manual smoke test if possible

### Task 2.6: Full controller test suite

- **Location**: `tests/test_controller.py` (new file)
- **Description**: Comprehensive tests using fake factories:
  - **Validation tests** (~5):
    - Empty API key → ValueError
    - No streams enabled → ValueError
    - No device index when stream enabled → ValueError
    - API key format warning → on_error called
    - Valid config → starts successfully
  - **Lifecycle tests** (~6):
    - start() creates workers via factories
    - start() starts all workers
    - stop() calls signal_stop on all workers
    - stop() calls join on all workers
    - stop() drains UI queue
    - toggle() alternates start/stop
  - **Audio dispatch tests** (~4):
    - on_audio_chunk routes "listen" to listen worker
    - on_audio_chunk routes "speak" to speak worker
    - Phase 0 (normal) uses build_prompt
    - Phase 1 (two_phase) uses build_stt_prompt
  - **PTT tests** (~3):
    - ptt_press sets event
    - ptt_release clears event
    - PTT event passed to capture factory
  - **Whisper mode tests** (~3):
    - use_whisper creates WhisperWorker via factory
    - Whisper callback wired to WhisperWorker.submit
    - Whisper mode uses faster rate interval
  - **Shutdown edge cases** (~4):
    - stop() when not running is safe
    - stop() with None workers is safe
    - Double stop is safe
    - start() after stop() works
  - **Config/callback tests** (~3):
    - on_error callback invoked for validation errors
    - on_status callback invoked during lifecycle
    - Default callbacks (None) don't crash
- **Complexity**: 6
- **Dependencies**: Tasks 2.1-2.4
- **Acceptance Criteria**:
  - ~25-30 test cases
  - No tkinter dependency in tests
  - No real audio devices or API calls
  - All tests run fast (<5s)
- **Validation**:
  - `python -m pytest tests/test_controller.py -v` — all pass

---

## Sprint 3: Integration & Cleanup

**Goal**: Ensure both refactors work together, clean up dead code, verify end-to-end.

**Demo/Validation**:
- Full test suite passes
- No dead code remains
- Code coverage improved

### Task 3.1: Integration test for strategy + controller

- **Location**: `tests/test_integration.py` (append)
- **Description**: Add 2-3 integration tests that exercise the full path:
  - Controller creates AudioCapture → strategy processes frames → callback fires → worker receives request
  - Use fake PyAudio-like objects and strategy injection
- **Complexity**: 3
- **Dependencies**: Sprints 1 and 2
- **Acceptance Criteria**:
  - End-to-end flow validated without real devices
- **Validation**:
  - Tests pass

### Task 3.2: Remove dead code and clean imports

- **Location**: `realtime_translator/audio.py`, `realtime_translator/app.py`
- **Description**:
  - Remove any dead mode-specific code left in `_record_loop` after strategy extraction
  - Clean up imports no longer needed in `app.py` (e.g., direct worker imports if only used via controller)
  - Verify `worker_utils.stop_worker_thread` is still used or remove if orphaned
- **Complexity**: 2
- **Dependencies**: Sprints 1 and 2 (no dependency on Task 3.1)
- **Acceptance Criteria**:
  - No unused imports
  - No dead code paths
- **Validation**:
  - All tests pass
  - `python -m py_compile realtime_translator/*.py` — no errors

### Task 3.3: Run simplify and final verification

- **Location**: All changed files
- **Description**: Run `/simplify` on all changes, then full test suite
- **Complexity**: 1
- **Dependencies**: Task 3.2
- **Acceptance Criteria**:
  - All tests pass
  - No code quality issues
- **Validation**:
  - `python -m pytest tests/ -q` — all tests pass

---

## Testing Strategy

| Layer | What | How |
|-------|------|-----|
| Unit | Record strategies | Synthetic PCM data, no PyAudio |
| Unit | TranslatorController | Fake factories, mock workers |
| Integration | Strategy + Controller + Workers | Fake factories, synthetic audio |
| Existing | ApiWorker, WhisperWorker, config, etc. | Unchanged, must still pass |

**Total new tests**: ~45-50 (15-20 strategies + 25-30 controller)
**Existing tests**: current pytest suite (must all pass)

## Potential Risks & Gotchas

1. **Strategy state boundary**: VAD strategy needs `VoiceActivityDetector` instance and `sample_rate` for threshold calculations. These must be passed at construction, not frame-by-frame. Ensure `_build_strategy()` has access to all needed values after PyAudio init.

2. **flush() timing**: When `_running` goes False, the main loop exits. `flush()` must be called AFTER the loop but BEFORE stream cleanup. The current code doesn't flush on stop (PTT frames are lost if you stop while holding the button). This is a minor behavior change — document it.

3. **Factory signature mismatch**: `AudioCapture` constructor has many params. The factory type should be `Callable[..., AudioCapture]` (using `...`) rather than enumerating all params, to avoid breaking when params change.

4. **Controller ↔ App boundary for PTT UI**: PTT button state (color, text) is purely UI. But PTT event is controller state. The app must observe controller state changes to update the button. Use callbacks or poll `controller.is_running`.

5. **Thread safety of controller state**: `start()`/`stop()`/`toggle()` run on the UI thread. However, `on_audio_chunk()` is called from AudioCapture recording threads. It reads `_api_worker_listen`/`_api_worker_speak`/`_whisper_worker` references which are set to `None` during `stop()` on the UI thread. There is a race: `on_audio_chunk` could read worker references between `signal_stop()` and the `None` assignment. This is the same race as the current code — not a regression. Mitigate with a `None` guard in `on_audio_chunk` (already present in current code).

6. **Callback exception handling in strategies**: Currently each mode wraps `self.callback()` in try/except. With strategies, the callback is invoked by the main loop's `_safe_callback()`. This centralizes error handling — verify the logging labels are still useful (currently mode-specific: "PTT", "VAD", "continuous").

7. **`_record_loop` error_callback**: The stream-level error callback (for PyAudio exceptions) stays in `_record_loop`, not in strategies. Strategies only handle frame-level logic.

8. **`audio_utils.py` already exists**: Contains `is_silent_pcm()`. Task 1.5 adds `frames_to_wav()` to this existing file — it is NOT a new file.

9. **Strategy unit tests vs. AudioCapture integration tests**: Strategy unit tests (Task 1.7) construct strategies directly with known values — no PyAudio needed. AudioCapture integration tests (if needed) would require mocking PyAudio. The optional `strategy` parameter on AudioCapture (Task 1.6) enables testing AudioCapture's loop logic with a mock strategy without PyAudio.

10. **Logging labels after centralizing callback error handling**: Current code logs mode-specific labels ("PTT", "VAD", "continuous"). After refactoring, `_safe_callback` won't know which mode is active. Either pass the strategy label to `_safe_callback`, or use a generic label like `self.label` (already available on AudioCapture). The generic label is sufficient since the strategy type is logged at startup.

11. **Controller callbacks must not be called from worker threads directly**: The app uses `_poll_queue()` to marshal worker→UI updates. Controller's `on_error`/`on_status` callbacks must only be called from the UI thread (during `start()`/`stop()` which run on UI thread). Audio thread callbacks (`on_audio_chunk`) must go through the `ui_queue`, not directly to tkinter methods. This constraint must be preserved during extraction.

12. **Sentinel delivery under queue backpressure**: `send_stop_sentinel()` silently drops the sentinel if the queue is full (`maxsize=3`). Under high load, `signal_stop()` may not deliver the sentinel, relying on `_running=False` + `get(timeout=1.0)` for the worker loop to exit. This means join timeout of 10s should always be sufficient (worst case: 1s poll + processing time), but stop latency can be up to ~1s longer under load. This is inherited behavior, not a regression.

13. **16-bit PCM assumption is preserved, not generalized**: `SAMPLE_WIDTH_BYTES` extraction (commit d5ee019) was for deduplication, not format generalization. All strategies, `frames_to_wav()`, and VAD remain 16-bit PCM (`pyaudio.paInt16`, `array("h")`) fixed. Do not introduce 24/32-bit paths in this refactor.

14. **Optional dependency flags are module-level constants**: `GENAI_AVAILABLE`/`WHISPER_AVAILABLE`/`WEBRTCVAD_AVAILABLE` are set at import time. Controller tests that need to vary these must use `unittest.mock.patch` on the specific module attribute (e.g., `patch("realtime_translator.controller.GENAI_AVAILABLE", False)`). Import ordering matters — ensure patches are applied before the code under test reads the flag.

## Rollback Plan

- Each sprint is independently committable
- Sprint 1 can be reverted without affecting Sprint 2 (strategies are additive)
- Sprint 2 can be reverted by restoring `app.py` to pre-extraction state
- Git branches: one branch per sprint recommended
- If strategy pattern proves too complex, the current `_record_loop` still works — strategies are purely additive until Task 1.6 rewires the loop

## File Change Summary

| File | Action | Sprint |
|------|--------|--------|
| `realtime_translator/record_strategies.py` | **NEW** | 1 |
| `realtime_translator/audio_utils.py` | Edit (add `frames_to_wav`) | 1 |
| `realtime_translator/audio.py` | Edit (simplify `_record_loop`) | 1 |
| `tests/test_record_strategies.py` | **NEW** | 1 |
| `realtime_translator/controller.py` | **NEW** | 2 |
| `realtime_translator/app.py` | Edit (delegate to controller) | 2 |
| `tests/test_controller.py` | **NEW** | 2 |
| `tests/test_integration.py` | Edit (add integration tests) | 3 |

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **[High] flush() behavior change**: Removed `strategy.flush()` call from `_record_loop` stop path. `flush()` is defined as API but not called during Sprint 1 stop (normal or error). Preserves current behavior where partial audio is discarded on stop. Updated Tasks 1.1, 1.3, 1.4, 1.6.
- **[High] GENAI_AVAILABLE/WHISPER_AVAILABLE validation**: Centralized all start-time validation in controller (Task 2.2). `app.py` must only collect raw UI state into `StartConfig`. Added explicit acceptance criteria for availability checks.
- **[High] ValueError + UI error contract**: Task 2.5 now specifies `_start()` as the exception boundary: `ValueError` → UI error only (no traceback), unexpected Exception → `logging.exception()` + UI error. Failed start preserves pre-start UI state.
- **[High] PTT guard**: Added `can_ptt` property to controller (Task 2.4). PTT event only set when running AND speak capture active. App delegates PTT UI changes only when `controller.can_ptt` permits.
- **[Medium] strategy param semantics**: Task 1.6 now explicitly states that custom `strategy` parameter bypasses auto-build completely and ignores mode params.
- **[Medium] Queue drain ordering**: Task 2.3 acceptance criteria now specifies: `signal_stop all → join all → drain queue → clear buffers → set _running=False`.
- **[Medium] API key format warning non-blocking**: Task 2.2 explicitly states format warning is non-blocking (calls `on_error` but does NOT raise `ValueError`).
- **[Low] Test count hardcoding**: Replaced "122 existing tests" with "existing pytest suite" throughout.
- **Hidden risks incorporated**: Added Risks 11-14 covering controller callback thread safety, sentinel delivery under backpressure, 16-bit PCM assumption, and module-level flag patching in tests.

### Skipped Feedback
- **[Medium] PyAudio dependency with strategy injection**: Strategy unit tests (Task 1.7) already test in isolation without PyAudio. AudioCapture integration tests are acknowledged as requiring `pa` fakes, but this is out of scope for Sprint 1 (strategies are tested standalone).
- **[Medium] WhisperWorkerFactory `...` type**: The `Callable[...]` type is intentionally loose to avoid coupling to WhisperWorker's constructor signature. The controller's `start()` method documents the actual arguments passed.
- **[Low] Test helper sharing**: Keeping `_make_silent_pcm`/`_make_sine_pcm` as local helpers in each test file is acceptable for this project's scale. Extracting to `tests/helpers.py` is optional future cleanup.
- **[Low] /simplify command**: This is a Claude Code skill, not a standalone CLI command. Kept as-is since the implementer will use Claude Code.
- **Stop-time queue drain losing messages**: This is inherited behavior from current code and explicitly documented in Risk 12. Changing drain semantics is a separate task.
