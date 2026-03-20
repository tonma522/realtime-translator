Section: `## File Map`
Replace:
`tests/test_integration.py` が「single-application guarantees」中心、`tests/test_history.py` が「Only if needed to confirm annotated translations are what enter history」になっている記述
With:
```md
- Modify: `tests/test_integration.py`
  - Verify both legacy 5-field and auto 8-field `translation_done` payloads, `output_language` resolution, transcript exclusion, and that annotated final text is what reaches history through `TranslatorApp._poll_queue()`
- Leave: `tests/test_history.py`
  - No Task 3 change unless the `TranslationHistory` data contract itself changes; annotated-history persistence is locked by the integration suite
```
Reason: Concern の本質は `TranslatorApp._poll_queue()` の統合面を固定できていない点なので、履歴格納の回帰も `tests/test_integration.py` 側で明示的に固定する、と冒頭で責務をはっきりさせるため。

Section: `### Task 3: Integrate Post-Processing Into Final Translation Events Only`
Replace:
Task 3 の **Files** と **Step 1-6** 全体
With:
```md
### Task 3: Integrate Post-Processing Into Final Translation Events Only

**Files:**
- Modify: `realtime_translator/app.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing integration tests that lock both `translation_done` shapes and annotated history persistence**

```python
def _make_poll_queue_app():
    from realtime_translator.app import TranslatorApp
    from realtime_translator.history import TranslationHistory

    app = object.__new__(TranslatorApp)
    app._ui_queue = queue.Queue()
    app._controller = MagicMock()
    app._controller.history = TranslationHistory()
    app._controller.is_running = False
    app._controller.can_retranslate.return_value = True
    app._controller.can_assist.return_value = True
    app._tools_panel = MagicMock()
    app.root = MagicMock()
    return app


def test_translation_done_legacy_tuple_annotates_with_fixed_output_language(mocker):
    from realtime_translator.app import TranslatorApp

    app = _make_poll_queue_app()
    annotate = mocker.patch(
        "realtime_translator.app.annotate_translation",
        return_value="12 mm (0.47 in, twelve millimeters)",
    )

    app._ui_queue.put(("translation_done", "listen_en_ja", "12:00:00", "12 mm", "12 mm"))
    TranslatorApp._poll_queue(app)

    annotate.assert_called_once_with("12 mm", output_language="ja")
    entry = app._controller.history.all_entries()[-1]
    assert entry.original == "12 mm"
    assert entry.translation == "12 mm (0.47 in, twelve millimeters)"
    assert entry.virtual_stream_id == "listen_en_ja"
    assert entry.resolved_direction == "en_ja"
    assert entry.error is None


def test_translation_done_auto_tuple_uses_resolved_direction_for_output_language(mocker):
    from realtime_translator.app import TranslatorApp

    app = _make_poll_queue_app()
    annotate = mocker.patch(
        "realtime_translator.app.annotate_translation",
        return_value="35 psi (0.24 MPa / 2.41 bar)",
    )

    app._ui_queue.put(
        ("translation_done", "listen", "listen_auto", "ja_en", "12:00:01", "35 psi", "35 psi", None)
    )
    TranslatorApp._poll_queue(app)

    annotate.assert_called_once_with("35 psi", output_language="en")
    entry = app._controller.history.all_entries()[-1]
    assert entry.translation == "35 psi (0.24 MPa / 2.41 bar)"
    assert entry.virtual_stream_id == "listen_auto"
    assert entry.resolved_direction == "ja_en"
    assert entry.error is None


def test_transcript_event_is_not_annotated_and_does_not_append_history(mocker):
    from realtime_translator.app import TranslatorApp

    app = _make_poll_queue_app()
    annotate = mocker.patch("realtime_translator.app.annotate_translation")

    app._ui_queue.put(("transcript", "listen_en_ja", "12:00:00", "12 mm"))
    TranslatorApp._poll_queue(app)

    annotate.assert_not_called()
    assert app._controller.history.all_entries() == []
```

- [ ] **Step 2: Run the targeted integration tests to confirm they fail**

Run: `pytest tests/test_integration.py -k "translation_done or transcript" -q`
Expected: FAIL because `app.py` still appends raw `translation` and does not resolve `output_language` for post-processing

- [ ] **Step 3: Resolve `output_language` from the final direction and annotate before history append**

```python
if resolved_direction == "en_ja":
    resolved_output_language = "ja"
elif resolved_direction == "ja_en":
    resolved_output_language = "en"
else:
    resolved_output_language = "ja" if mode == "en_ja" else "en"

annotated_translation = annotate_translation(
    translation,
    output_language=resolved_output_language,
)

entry = self._controller.history.append(
    source_stream_id,
    ts,
    original,
    annotated_translation,
    virtual_stream_id=virtual_stream_id,
    resolved_direction=resolved_direction,
    error=error,
)
```

- [ ] **Step 4: Preserve the routing boundary explicitly**

Run: keep `_on_transcript(...)`, `assist_result`, `assist_error`, and minutes handling unchanged, and continue accepting both the legacy 5-field and auto 8-field `translation_done` event shapes

- [ ] **Step 5: Treat annotated history persistence as part of the integration contract**

Run: keep the history assertions in `tests/test_integration.py`; do not add a call-count-only regression in place of entry-content assertions

- [ ] **Step 6: Run focused regressions**

Run: `pytest tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/app.py tests/test_integration.py
git commit -m "feat: annotate final translation outputs"
```
```
Reason: `call_count` だけでは仕様の固定にならないため、5 要素と 8 要素の両経路、`resolved_direction` からの `output_language` 解決、`transcript` 除外、そして history に注釈済み文字列が入ることを同じ統合テスト群で直接アサートする形に置き換えるため。