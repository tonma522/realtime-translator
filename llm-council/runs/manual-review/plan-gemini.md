Data collection is disabled.
Loaded cached credentials.
--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\tests\__pycache__\__init__.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\tests\__pycache__\test_audio.cpython-312-pytest-8.3.4.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\tests\__pycache__\test_config.cpython-312-pytest-8.3.4.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\tests\__pycache__\test_imports.cpython-312-pytest-8.3.4.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\tests\__pycache__\test_prompts.cpython-312-pytest-8.3.4.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\__init__.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\__main__.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\api.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\app.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\audio.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\audio_utils.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\config.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\constants.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\devices.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\prompts.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\vad.cpython-312.pyc ---

<binary file>

--- C:\Users\tenx5\OneDrive - Extrude Hone GmbH\ドキュメント\cursordoc\100 - プロジェクト管理\active\realtime-translator\realtime_translator\__pycache__\whisper_stt.cpython-312.pyc ---

<binary file><ctrl46>}1. Severity: Medium
2. Category: Quality
3. Location: `realtime_translator/config.py:52`
4. Description: `save_config` mutates the caller's dictionary by using `data.pop("api_key", "")`. This side effect can cause unexpected behavior if the caller expects the dictionary to remain intact after saving.
5. Recommendation: Create a shallow copy of the dictionary or use `data.get("api_key")` without mutating the original object.

1. Severity: High
2. Category: Reliability
3. Location: `realtime_translator/app.py:198` (`_start`)
4. Description: If `_start_inner` raises an exception after starting worker threads (e.g., during audio capture initialization), the already started threads are not stopped. This leads to resource leaks and zombie threads running in the background.
5. Recommendation: Call `self._stop()` in the `except` block of `_start()` to ensure all partially initialized resources are properly cleaned up.

1. Severity: Medium
2. Category: Architecture
3. Location: `realtime_translator/audio.py:48`
4. Description: The `_record_loop` method is monolithic, handling PTT, VAD, and continuous modes in a single large loop with complex branching. This makes the audio capture logic hard to read, maintain, and test.
5. Recommendation: Extract the logic for each recording mode into separate helper methods or implement a Strategy pattern for chunk processing.

1. Severity: High
2. Category: Testing
3. Location: `realtime_translator/api.py`, `realtime_translator/whisper_stt.py`
4. Description: Core business logic components (`ApiWorker`, `WhisperWorker`) lack unit tests. This significantly increases the risk of regressions during refactoring or feature additions.
5. Recommendation: Implement unit tests for these workers using mocked dependencies (e.g., mocking the Gemini client, Whisper model, and queues).

1. Severity: Low
2. Category: Performance
3. Location: `realtime_translator/whisper_stt.py:21`
4. Description: `WhisperModel` is hardcoded to use `device="cpu"`. While this avoids CUDA DLL conflicts on Windows, it prevents users with capable GPUs from benefiting from hardware acceleration, which significantly improves STT performance.
5. Recommendation: Make the compute device configurable via `config.py` and the UI, allowing users to opt-in to CUDA with a graceful fallback to CPU if initialization fails.

# Plan

## Overview
This plan addresses code quality, reliability, and testability issues identified in the codebase. The approach focuses on eliminating side effects, fixing resource leaks during startup, refactoring complex audio loops, and establishing a testing foundation for core background workers.

## Scope
- In: Fixing config dictionary mutation, fixing thread leaks on startup failure, refactoring `AudioCapture`, adding Whisper GPU support, and writing unit tests for API/Whisper workers.
- Out: Complete UI rewrite, changing the underlying audio library (PyAudio), or modifying the core Gemini API integration logic.

## Phases
### Phase 1: Reliability & Code Quality
**Goal**: Eliminate side effects, prevent resource leaks, and improve the maintainability of the audio capture module.

#### Task 1.1: Fix dictionary mutation in `save_config`
- Location: `realtime_translator/config.py:52`
- Description: Modify `save_config` to avoid mutating the passed `data` dictionary.
- Estimated Tokens: 50
- Dependencies: None
- Steps:
  - Change `api_key = data.pop("api_key", "")` to `api_key = data.get("api_key", "")`.
  - Create a copy of data for saving: `save_data = {k: v for k, v in data.items() if k != "api_key"}`.
  - Update the JSON serialization to use `save_data`.
- Acceptance Criteria:
  - Calling `save_config(d)` does not modify the original dictionary `d`.
  - Existing tests in `test_config.py` pass.

#### Task 1.2: Fix resource leak on startup failure
- Location: `realtime_translator/app.py:198`
- Description: Ensure `self._stop()` is called if `_start_inner()` fails, to clean up partially started threads.
- Estimated Tokens: 20
- Dependencies: None
- Steps:
  - In `_start()`, add a call to `self._stop()` inside the `except Exception as e:` block before appending the error message to the UI.
- Acceptance Criteria:
  - Forcing an error in `_start_inner` after thread creation results in those threads being successfully terminated.

#### Task 1.3: Refactor `AudioCapture._record_loop`
- Location: `realtime_translator/audio.py`
- Description: Break down the monolithic `_record_loop` into smaller methods for each mode (PTT, VAD, Continuous).
- Estimated Tokens: 400
- Dependencies: None
- Steps:
  - Extract the PTT logic into a `_process_ptt_mode` method.
  - Extract the VAD logic into a `_process_vad_mode` method.
  - Extract the Continuous logic into a `_process_continuous_mode` method.
  - Update `_record_loop` to call the appropriate method based on the active mode.
- Acceptance Criteria:
  - Audio capture works exactly as before in all three modes.
  - Code complexity of `_record_loop` is significantly reduced.

### Phase 2: Performance & Testing
**Goal**: Improve STT performance for capable hardware and increase test coverage for core workers.

#### Task 2.1: Configurable Whisper Device (CPU/GPU)
- Location: `realtime_translator/whisper_stt.py`, `realtime_translator/app.py`, `realtime_translator/config.py`
- Description: Allow users to select the compute device (CPU or CUDA) for Whisper.
- Estimated Tokens: 300
- Dependencies: None
- Steps:
  - Add a `whisper_device` setting to `config.py` (default "cpu").
  - Add a UI dropdown in `app.py` to select "cpu" or "cuda".
  - Update `WhisperTranscriber.__init__` to accept the device parameter.
  - Add a try-except block around `WhisperModel` initialization to fallback to CPU if CUDA fails.
- Acceptance Criteria:
  - Users can select CUDA in the UI.
  - If CUDA is selected but unavailable, it falls back to CPU gracefully and shows a warning in the UI.

#### Task 2.2: Unit Tests for `ApiWorker`
- Location: `tests/test_api.py`
- Description: Add unit tests for `ApiWorker` to verify queue management and phase transitions.
- Estimated Tokens: 500
- Dependencies: None
- Steps:
  - Create `tests/test_api.py`.
  - Mock the Gemini client and `ui_queue`.
  - Test that `submit` drops the oldest request when the queue is full.
  - Test that Phase 1 correctly transitions to Phase 2 upon receiving a transcript.
- Acceptance Criteria:
  - `pytest` runs and passes the new tests.
  - Coverage for `api.py` increases significantly.

## Testing Strategy
- Run the existing test suite (`pytest`) after every task to ensure no regressions in config or audio utilities.
- Manually test the UI after Task 1.2 by temporarily injecting a `raise Exception("Test")` in `_start_inner` to verify threads are cleaned up.
- Manually test all three audio modes (PTT, VAD, Continuous) after Task 1.3 to ensure the refactoring didn't break functionality.
- For Task 2.1, test on a machine without CUDA to verify the fallback mechanism works without crashing the app.

## Risks
- **Risk**: Refactoring `AudioCapture` might introduce subtle bugs in audio chunking, leading to dropped audio or misaligned VAD.
  - **Mitigation**: Keep the logic identical during extraction. Perform rigorous manual testing of edge cases (e.g., very short utterances, rapid PTT toggling).
- **Self-critique 1**: The plan suggests adding GPU support for Whisper, but doesn't account for the massive dependency size of CUDA libraries or the complexity of handling fallback gracefully if CUDA is installed but broken (e.g., wrong cuDNN version). This could lead to application crashes on startup or long hangs during model initialization that freeze the UI.
- **Self-critique 2**: Extracting logic in `AudioCapture` might increase function call overhead in a tight audio loop. While Python function calls are relatively fast, doing it per-frame (e.g., in VAD mode) might introduce slight latency. If the overhead is too high, it could cause audio buffer overflows.

## Rollback Plan
- All changes will be made in feature branches.
- If a task introduces critical bugs, the branch will be reverted using `git revert <commit-hash>`.
- For Task 1.1 (config mutation), if the new save logic breaks existing config files, we will revert to the mutating version and add a deepcopy in `app.py` instead.

## Edge Cases
- **Queue Overflow**: When testing `ApiWorker`, ensure we test the exact boundary condition (submitting the 4th item to a maxsize=3 queue).
- **CUDA Fallback**: If a user selects CUDA, but their GPU lacks sufficient VRAM for the selected model size, `faster-whisper` might throw an OutOfMemory error during transcription, not initialization. The fallback must handle runtime errors, not just initialization errors.
- **Rapid PTT**: Pressing and releasing the PTT button faster than the audio chunk size (e.g., < 50ms) might result in empty WAV files being sent to the API.

## Open Questions
- Should we migrate from `tkinter` to a more modern UI framework (e.g., PyQt or CustomTkinter) in the future to better handle async operations and modern styling?
- Is the 4.0s rate limit for Gemini Free tier strictly enforced per minute, or is it a rolling window? Can we optimize the queue to burst requests if we haven't hit the limit recently?
