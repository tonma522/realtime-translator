# Main UI Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the main Tkinter window into `left controls + center timeline + right workspace tabs` while preserving translation, retranslation, assist, and minutes behavior.

**Architecture:** Introduce a thin UI contract layer first so `TranslatorApp` remains the event hub but no longer owns detailed widget layout. Move persistent session summary, status resolution, and UI error normalization into focused helpers/panels, then swap the layout in phases so each intermediate step is testable. Keep `settings_window` as the editing surface for detailed configuration and reflect its values back into the main window through explicit panel APIs.

**Tech Stack:** Python 3.13, tkinter/ttk, pytest, unittest.mock

---

## File Map

- Create: `realtime_translator/ui_state.py`
  - `UiError`, `SessionSummary`, `GlobalStatusResolver`, legacy error normalization helpers
- Create: `realtime_translator/main_controls_panel.py`
  - Left column UI, start/stop CTA, stream toggles, summary labels, blocker rendering
- Create: `realtime_translator/translation_timeline_panel.py`
  - Center timeline widget, status bar rendering, partial/final/error insertion helpers
- Create: `realtime_translator/workspace_panel.py`
  - Right-side `ttk.Notebook` workspace for retranslation / assist / minutes
- Modify: `realtime_translator/app.py`
  - Replace current top-toggle + vertical pane layout with panel composition
  - Route queue events through `ui_state.py` + panel APIs
- Modify: `realtime_translator/settings_window.py`
  - Add explicit change notifications for non-`StringVar` / `Text` driven updates
- Modify: `realtime_translator/tools_panel.py`
  - Either remove usage entirely or convert into a compatibility shim that delegates to `WorkspacePanel`
- Modify: `realtime_translator/config.py`
  - Only if needed to expose normalized summary helpers cleanly for the main controls
- Test: `tests/test_ui_state.py`
  - Error normalization, status precedence, session summary formatting
- Test: `tests/test_main_controls_panel.py`
  - Summary rendering, blocker display, immediate settings reflection
- Test: `tests/test_translation_timeline_panel.py`
  - Partial/final/error rendering, status bar updates
- Test: `tests/test_workspace_panel.py`
  - Notebook tabs, tool state persistence, cross-tab event handling
- Modify: `tests/test_tools_panel.py`
  - Replace with notebook/workspace expectations if `ToolsPanel` is retired
- Modify: `tests/test_controller.py`
  - Add coverage for event payloads used by UI normalization
- Modify: `tests/test_integration.py`
  - Add regression coverage for main-window event flow and cross-tab behavior

## Pre-Implementation Decisions To Freeze

- These decisions satisfy the spec requirement that the remaining 3 UI unknowns be resolved before implementation work begins.
- Left summary granularity: show one line per stream direction and one compact line per mode family (`録音モード`, `翻訳方式`, `原文表示`)
- Blocker rendering: use inline warning card near the start button, not a plain one-line label
- Right workspace minimum width: reserve a fixed minimum width token in `app.py` layout constants and verify at the small-window smoke test
- Step 1 implementation target: before the new panel classes are mounted, use lightweight adapter objects or shim methods so `TranslatorApp` can call the new API shape against existing widgets
- Legacy `("error", stream_id, msg)` normalization:
  - startup validation failures raised before `controller.start()` -> `scope=session`, `severity=blocker`, `source=startup`
  - runtime queue errors from translation workers -> `scope=session`, `severity=runtime`, `source=translation`
  - assist/retranslation/minutes callbacks -> `scope=tool`, `severity=runtime`, `source=<tool>`

## Verification Ladder

- Narrow tests after each task
- `pytest tests/test_ui_state.py tests/test_main_controls_panel.py tests/test_translation_timeline_panel.py tests/test_workspace_panel.py -q`
- Focused regressions after integration steps
- `pytest tests/test_controller.py tests/test_tools_panel.py tests/test_integration.py -q`
- Final regression
- `pytest -q`

### Task 1: Define UI Contracts Before Moving Widgets

**Files:**
- Create: `realtime_translator/ui_state.py`
- Test: `tests/test_ui_state.py`
- Modify: `realtime_translator/app.py`

- [ ] **Step 1: Write the failing contract tests**

```python
def test_normalize_legacy_translation_error_to_session_runtime():
    event = ("error", "listen", "API limit exceeded")
    normalized = normalize_ui_error(event, source_hint="translation")
    assert normalized.scope == "session"
    assert normalized.severity == "runtime"


def test_global_status_resolver_prefers_error_over_ptt():
    resolver = GlobalStatusResolver()
    status = resolver.resolve(
        session_error="起動エラー",
        ptt_recording=True,
        running=True,
        initializing=False,
    )
    assert status.kind == "error"
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `pytest tests/test_ui_state.py -q`
Expected: FAIL because `ui_state.py` does not exist yet

- [ ] **Step 3: Implement the minimal contract layer**

```python
@dataclass(frozen=True)
class UiError:
    scope: Literal["session", "tool"]
    severity: Literal["blocker", "runtime"]
    source: str
    message: str


class GlobalStatusResolver:
    def resolve(self, *, session_error, ptt_recording, running, initializing):
        ...
```

- [ ] **Step 4: Add `TranslatorApp` helpers that call the contract layer without changing the layout yet**

Run: wire a new `_normalize_error_event(...)` and `_resolve_global_status(...)` into `app.py`, but keep existing widgets for now

- [ ] **Step 5: Run the contract tests again**

Run: `pytest tests/test_ui_state.py -q`
Expected: PASS

- [ ] **Step 6: Run a focused regression on existing controller/UI interaction tests**

Run: `pytest tests/test_controller.py tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/ui_state.py realtime_translator/app.py tests/test_ui_state.py tests/test_controller.py tests/test_integration.py
git commit -m "refactor: add UI state contracts"
```

### Task 2: Replace the 3-Column Tools Panel With a Notebook Workspace

**Files:**
- Create: `realtime_translator/workspace_panel.py`
- Modify: `realtime_translator/tools_panel.py`
- Test: `tests/test_workspace_panel.py`
- Modify: `tests/test_tools_panel.py`

- [ ] **Step 1: Write failing notebook behavior tests**

```python
def test_workspace_has_three_tabs(root, controller):
    panel = WorkspacePanel(root, controller)
    assert panel.tab_labels() == ["再翻訳", "返答アシスト", "議事録"]


def test_history_update_does_not_switch_active_tab(root, controller):
    panel = WorkspacePanel(root, controller)
    panel.select_tab("議事録")
    panel.on_history_entry(entry)
    assert panel.active_tab_label() == "議事録"
```

- [ ] **Step 2: Run the new workspace tests to confirm they fail**

Run: `pytest tests/test_workspace_panel.py -q`
Expected: FAIL because `WorkspacePanel` does not exist yet

- [ ] **Step 3: Implement `WorkspacePanel` by moving current `ToolsPanel` logic into notebook tabs**

```python
self._notebook = ttk.Notebook(parent)
self._retranslation_tab = ttk.Frame(self._notebook)
self._assist_tab = ttk.Frame(self._notebook)
self._minutes_tab = ttk.Frame(self._notebook)
```

- [ ] **Step 4: Keep `ToolsPanel` as a temporary compatibility wrapper or replace its imports in one pass**

Run: if keeping compatibility, make `ToolsPanel.frame` point to `WorkspacePanel.frame` and delegate public methods

- [ ] **Step 5: Run workspace and legacy tool-panel tests**

Run: `pytest tests/test_workspace_panel.py tests/test_tools_panel.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add realtime_translator/workspace_panel.py realtime_translator/tools_panel.py tests/test_workspace_panel.py tests/test_tools_panel.py
git commit -m "refactor: convert tool area to notebook workspace"
```

### Task 3: Extract the Translation Timeline Panel

**Files:**
- Create: `realtime_translator/translation_timeline_panel.py`
- Modify: `realtime_translator/app.py`
- Test: `tests/test_translation_timeline_panel.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing timeline tests**

```python
def test_partial_sequence_appends_header_and_text(root):
    panel = TranslationTimelinePanel(root)
    panel.on_partial_start("listen_auto", "12:00:00")
    panel.on_partial("listen_auto", "hello")
    assert "hello" in panel.dump_text()


def test_runtime_error_renders_without_overwriting_status(root):
    panel = TranslationTimelinePanel(root)
    panel.set_global_status("running", "状態: 翻訳中")
    panel.on_runtime_error("listen", "API limit exceeded")
    assert "API limit exceeded" in panel.dump_text()
```

- [ ] **Step 2: Run the timeline tests to confirm they fail**

Run: `pytest tests/test_translation_timeline_panel.py -q`
Expected: FAIL because `TranslationTimelinePanel` does not exist yet

- [ ] **Step 3: Implement the panel with dedicated status bar + text area helpers**

```python
class TranslationTimelinePanel:
    def set_global_status(self, status_kind: str, message: str) -> None:
        ...
    def on_partial_start(self, stream_id: str, ts: str) -> None:
        ...
```

- [ ] **Step 4: Rewire `TranslatorApp._poll_queue()` and helper methods to call the new panel**

Run: replace direct `_result_text` writes in `_on_partial_*`, `_on_transcript`, `_append_error` with panel method calls

- [ ] **Step 5: Run focused UI regressions**

Run: `pytest tests/test_translation_timeline_panel.py tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add realtime_translator/translation_timeline_panel.py realtime_translator/app.py tests/test_translation_timeline_panel.py tests/test_integration.py
git commit -m "refactor: extract translation timeline panel"
```

### Task 4: Build the Left Main Controls Panel and Immediate Summary Sync

**Files:**
- Create: `realtime_translator/main_controls_panel.py`
- Modify: `realtime_translator/settings_window.py`
- Modify: `realtime_translator/app.py`
- Test: `tests/test_main_controls_panel.py`

- [ ] **Step 1: Write failing tests for summaries and blocker UI**

```python
def test_session_summary_shows_direction_and_mode(root):
    panel = MainControlsPanel(root, on_toggle=lambda: None, on_open_settings=lambda: None)
    panel.apply_session_summary(
        listen_enabled=True,
        speak_enabled=True,
        pc_audio_label="PC音声: 英語→日本語",
        mic_label="マイク: 日本語→英語",
        mode_summary=["録音モード: PTT", "翻訳方式: 通常"],
    )
    assert "PC音声: 英語→日本語" in panel.dump_labels()


def test_blocker_card_visible_when_message_present(root):
    panel = MainControlsPanel(root, on_toggle=lambda: None, on_open_settings=lambda: None)
    panel.set_blocker("APIキーが未設定")
    assert panel.blocker_visible() is True
```

- [ ] **Step 2: Run the main controls tests to confirm they fail**

Run: `pytest tests/test_main_controls_panel.py -q`
Expected: FAIL because `MainControlsPanel` does not exist yet

- [ ] **Step 3: Implement the left panel with CTA, stream toggles, summary labels, and blocker card**

```python
self._start_button = ttk.Button(...)
self._listen_checkbox = ttk.Checkbutton(...)
self._blocker_frame = ttk.Frame(...)
```

- [ ] **Step 4: Add explicit settings-to-main-window sync hooks**

Run: in `settings_window.py`, connect `trace_add(...)` and text-change callbacks so `TranslatorApp` can rebuild the session summary immediately

- [ ] **Step 5: Recompute summaries in `app.py` through a single `_apply_session_summary()` helper**

Run: include direction labels, mode summaries, and start/stop label updates through `MainControlsPanel`

- [ ] **Step 6: Run panel + settings sync tests**

Run: `pytest tests/test_main_controls_panel.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/main_controls_panel.py realtime_translator/settings_window.py realtime_translator/app.py tests/test_main_controls_panel.py
git commit -m "refactor: add main controls panel"
```

### Task 5: Swap the Main Window Layout to Left / Center / Right

**Files:**
- Modify: `realtime_translator/app.py`
- Modify: `realtime_translator/__main__.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write/extend a failing layout smoke test**

```python
def test_main_window_uses_three_region_layout():
    app = build_app_for_test()
    assert app._main_controls_panel is not None
    assert app._timeline_panel is not None
    assert app._workspace_panel is not None
```

- [ ] **Step 2: Run the layout smoke test to confirm the current layout still fails it**

Run: `pytest tests/test_integration.py -k "three_region_layout" -q`
Expected: FAIL

- [ ] **Step 3: Replace the old top stream frame + vertical paned layout with the three-region composition**

```python
shell = ttk.Frame(self.root)
shell.columnconfigure(0, minsize=left_controls_min_width)
shell.columnconfigure(1, weight=1)
shell.columnconfigure(2, minsize=workspace_min_width)
```

- [ ] **Step 4: Preserve minimum window usability**

Run: keep `root.minsize(...)` and add explicit min width handling for the workspace column token decided earlier

- [ ] **Step 5: Run focused integration tests**

Run: `pytest tests/test_integration.py tests/test_main_controls_panel.py tests/test_workspace_panel.py tests/test_translation_timeline_panel.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add realtime_translator/app.py realtime_translator/__main__.py tests/test_integration.py
git commit -m "refactor: adopt three-region main window layout"
```

### Task 6: Finalize Error Routing, Boundary Cases, and Full Regression

**Files:**
- Modify: `realtime_translator/app.py`
- Modify: `realtime_translator/ui_state.py`
- Modify: `tests/test_controller.py`
- Modify: `tests/test_integration.py`
- Modify: `tests/test_workspace_panel.py`
- Modify: `tests/test_translation_timeline_panel.py`

- [ ] **Step 1: Write failing tests for the remaining medium review items**

```python
def test_legacy_error_event_normalizes_to_blocker_or_runtime():
    ...


def test_step1_adapter_updates_existing_widgets_before_panel_swap():
    ...


def test_ptt_keybindings_survive_layout_swap():
    ...
```

- [ ] **Step 2: Run those targeted tests to confirm they fail**

Run: `pytest tests/test_ui_state.py tests/test_integration.py -k "normalize or adapter" -q`
Expected: FAIL

- [ ] **Step 3: Lock down API argument shapes and legacy error normalization rules in code**

```python
SessionSummary(
    listen_enabled: bool,
    speak_enabled: bool,
    pc_audio_label: str,
    mic_label: str,
    mode_summary: tuple[str, ...],
)
```

- [ ] **Step 4: Add boundary-case regressions**

Run:
- active tab remains stable during incoming translation events
- session blocker + tool local error display separation
- PTT keyboard handling remains intact after layout swap

- [ ] **Step 5: Run the focused regression suite**

Run: `pytest tests/test_ui_state.py tests/test_controller.py tests/test_workspace_panel.py tests/test_translation_timeline_panel.py tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/app.py realtime_translator/ui_state.py tests/test_controller.py tests/test_workspace_panel.py tests/test_translation_timeline_panel.py tests/test_integration.py
git commit -m "test: finalize main UI refactor regressions"
```

## Manual Smoke Checklist

- [ ] 起動直後に左カラムへ `開始/停止・聴く/話す・方向サマリー・モード要約` が見える
- [ ] `詳細設定` で方向やモードを変更すると左カラム要約が即時更新される
- [ ] 翻訳実行中に中央状態バーが `翻訳中` / `PTT録音中` / `エラー` を優先順位どおり表示する
- [ ] 再翻訳 / 返答アシスト / 議事録をタブ切替しても結果と入力状態が保持される
- [ ] 別タブを開いている状態で翻訳イベントが流れても、タイムラインと履歴更新が壊れない
- [ ] 小さめのウィンドウ幅でも CTA と右タブが隠れない

## Final Verification Commands

```bash
pytest tests/test_ui_state.py tests/test_main_controls_panel.py tests/test_translation_timeline_panel.py tests/test_workspace_panel.py -q
pytest tests/test_controller.py tests/test_tools_panel.py tests/test_integration.py -q
pytest -q
```

## Handoff Notes

- The first implementation step must freeze the adapter/signature choices from the peer review loop before any widget relocation.
- Do not delete `tools_panel.py` until all imports and tests have moved to `workspace_panel.py`.
- Keep commits small and in task order; each task should leave the application runnable.
