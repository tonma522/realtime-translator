Section: Task 2: Build The Deterministic Post-Processing Core
Replace:
`### Task 2: Build The Deterministic Post-Processing Core` セクション全体
With:
```md
### Task 2: Build The Deterministic Post-Processing Core

**Files:**
- Create: `realtime_translator/unit_tables.py`
- Create: `realtime_translator/translation_postprocess.py`
- Create: `tests/test_translation_postprocess.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_openai_llm.py`

- [ ] **Step 1: Write failing pure-function tests and upstream raw-output contract tests**

```python
def test_length_annotation_adds_inches():
    assert annotate_translation("12 mm", output_language="en") == "12 mm (0.47 in, twelve millimeters)"


def test_pressure_annotation_from_psi_adds_mpa_and_bar():
    assert annotate_translation("35 psi", output_language="ja") == "35 psi (0.24 MPa / 2.41 bar)"


def test_identifier_is_not_read_as_spoken_number():
    assert annotate_translation("Use M8 bolt", output_language="en") == "Use M8 bolt"


def test_tolerance_keeps_precision():
    assert "0.0004 in" in annotate_translation("±0.01 mm", output_language="en")


def test_api_worker_translation_done_payload_stays_raw_text():
    done = [m for m in messages if m[0] == "translation_done"][0]
    assert done[6] == "こんにちは"


def test_openai_llm_translation_done_payload_stays_raw_text():
    done = [m for m in messages if m[0] == "translation_done"][0]
    assert done[6] == "Hello"
```

- [ ] **Step 2: Run the targeted post-processing and upstream contract tests to confirm they fail**

Run: `pytest tests/test_translation_postprocess.py tests/test_api.py tests/test_openai_llm.py -q`
Expected: FAIL because the post-processing module does not exist yet and the raw-output contract tests are not present yet

- [ ] **Step 3: Add `unit_tables.py` with fixed mappings and documented constants**

```python
MM_PER_INCH = Decimal("25.4")
LB_PER_KG = Decimal("2.20462")
PSI_PER_MPA = Decimal("145.037738")
PSI_PER_BAR = Decimal("14.5037738")
ABRASIVE_TABLE = {
    "#400": {"micron": 35, "fepa": "P400"},
}
```

- [ ] **Step 4: Implement the minimal public API in `translation_postprocess.py`**

```python
def annotate_translation(text: str, *, output_language: str) -> str:
    ...
```

- [ ] **Step 5: Lock required behaviors before widening scope**

Run tests for:
- engineering units: `mm / cm / m / in / ft`, `g / kg / lb`, `C / F`, `Nm / lbf·ft`, `MPa / bar / psi`
- `Ra`
- `JIS # / FEPA P / mesh / micron`
- ambiguity exclusions: identifiers, dates, versions, bare `grit`
- pure-function partial failure fallback
- `realtime_translator/api.py` and `realtime_translator/openai_llm.py` continue to emit raw `translation_done` text and do not import the post-processing layer

- [ ] **Step 6: Run the focused suites**

Run: `pytest tests/test_translation_postprocess.py tests/test_api.py tests/test_openai_llm.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/unit_tables.py realtime_translator/translation_postprocess.py tests/test_translation_postprocess.py tests/test_api.py tests/test_openai_llm.py
git commit -m "feat: add deterministic translation annotation pipeline"
```
```
Reason: upstream の `api.py` / `openai_llm.py` が raw translation を流し続ける契約を Task 2 で固定しないと、後続の統合で責務境界が崩れても検知できないため。

Section: Task 3: Integrate Post-Processing Into Final Translation Events Only
Replace:
`### Task 3: Integrate Post-Processing Into Final Translation Events Only` セクション全体
With:
```md
### Task 3: Integrate Post-Processing Into Final Translation Events Only

**Files:**
- Modify: `realtime_translator/app.py`
- Modify: `tests/test_integration.py`
- Modify: `tests/test_history.py`

- [ ] **Step 1: Write failing integration tests for final-vs-transcript routing and annotation-failure fallback**

```python
def test_translation_done_is_annotated_before_history_append(app, mocker):
    spy = mocker.patch("realtime_translator.app.annotate_translation", return_value="annotated")
    app._ui_queue.put(("translation_done", "listen_en_ja", "12:00:00", "12 mm", "12 mm"))
    app._poll_queue()
    assert spy.call_count == 1
    assert app._controller.history.all_entries()[-1].translation == "annotated"


def test_translation_done_annotation_failure_falls_back_to_raw(app, mocker):
    mocker.patch("realtime_translator.app.annotate_translation", side_effect=RuntimeError("boom"))
    app._ui_queue.put(("translation_done", "listen_en_ja", "12:00:00", "12 mm", "12 mm"))
    app._poll_queue()
    assert app._controller.history.all_entries()[-1].translation == "12 mm"


def test_transcript_event_is_not_annotated(app, mocker):
    spy = mocker.patch("realtime_translator.app.annotate_translation")
    app._ui_queue.put(("transcript", "listen_en_ja", "12:00:00", "12 mm"))
    app._poll_queue()
    assert spy.call_count == 0
```

- [ ] **Step 2: Run the targeted integration tests to confirm they fail**

Run: `pytest tests/test_integration.py -k "annotated or transcript or fallback" -q`
Expected: FAIL because `app.py` does not call the post-processing layer yet and has no fallback behavior

- [ ] **Step 3: Import and apply `annotate_translation(...)` only in the `translation_done` branch, with raw-text fallback on exception**

```python
try:
    annotated_translation = annotate_translation(
        translation,
        output_language=resolved_output_language,
    )
except Exception:
    logging.exception("translation annotation failed; keeping raw translation")
    annotated_translation = translation
```

- [ ] **Step 4: Preserve exclusions and upstream raw contract in code**

Run: leave `_on_transcript(...)`, `assist_result`, `assist_error`, and minutes handling untouched, and do not change how `realtime_translator/api.py` or `realtime_translator/openai_llm.py` populate `translation_done`

- [ ] **Step 5: Confirm history receives annotated final output or the raw fallback output**

Run: update the history assertion path only if existing tests depend on raw text

- [ ] **Step 6: Run focused regressions**

Run: `pytest tests/test_integration.py tests/test_history.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/app.py tests/test_integration.py tests/test_history.py
git commit -m "feat: annotate final translation outputs"
```
```
Reason: 注釈統合点の例外で `translation_done` 自体を落とさず raw translation を履歴へ残す fallback を、UI 統合箇所で明示的に担保する必要があるため。

Section: Task 4: Integrate Retranslation Without Touching Assist Or Minutes
Replace:
`### Task 4: Integrate Retranslation Without Touching Assist Or Minutes` セクション全体
With:
```md
### Task 4: Integrate Retranslation Without Touching Assist Or Minutes

**Files:**
- Modify: `realtime_translator/retranslation.py`
- Modify: `tests/test_retranslation.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing retranslation tests for single application and annotation-failure fallback**

```python
def test_retranslation_result_is_annotated_once(mocker, worker):
    mocker.patch.object(worker, "_call_gemini", return_value="35 psi")
    spy = mocker.patch(
        "realtime_translator.retranslation.annotate_translation",
        return_value="annotated",
    )
    result = worker._execute_retranslation(req, client)
    assert result == "annotated"
    assert spy.call_count == 1


def test_retranslation_annotation_failure_falls_back_to_raw(mocker, worker):
    mocker.patch.object(worker, "_call_gemini", return_value="35 psi")
    mocker.patch(
        "realtime_translator.retranslation.annotate_translation",
        side_effect=RuntimeError("boom"),
    )
    result = worker._execute_retranslation(req, client)
    assert result == "35 psi"


def test_assist_and_minutes_paths_do_not_call_annotation(app, mocker):
    spy = mocker.patch("realtime_translator.app.annotate_translation")
    app._ui_queue.put(("assist_result", "req1", "reply_assist", "hello"))
    app._ui_queue.put(("assist_result", "req2", "minutes", "hello"))
    app._poll_queue()
    assert spy.call_count == 0
```

- [ ] **Step 2: Run the targeted tests to confirm they fail**

Run: `pytest tests/test_retranslation.py tests/test_integration.py -k "annotat or fallback" -q`
Expected: FAIL because retranslation still returns raw text and has no annotation-failure fallback

- [ ] **Step 3: Apply post-processing inside `_execute_retranslation(...)`, with raw-text fallback on exception**

```python
result = self._call_gemini(client, prompt)
try:
    return annotate_translation(result, output_language=dst_language)
except Exception:
    logging.exception("retranslation annotation failed; keeping raw translation")
    return result
```

- [ ] **Step 4: Keep assist and minutes workers outside this feature, and keep upstream translation workers raw**

Run: do not import or call the post-processing layer from `assist.py`, `workspace_panel.py`, `api.py`, or `openai_llm.py`

- [ ] **Step 5: Run focused regressions**

Run: `pytest tests/test_retranslation.py tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add realtime_translator/retranslation.py tests/test_retranslation.py tests/test_integration.py
git commit -m "feat: annotate retranslation outputs"
```
```
Reason: 再翻訳経路でも注釈失敗時に raw translation を返す fallback を固定しつつ、assist/minutes と upstream worker 群へ責務が漏れないことを同じ Task 内で明示する必要があるため。