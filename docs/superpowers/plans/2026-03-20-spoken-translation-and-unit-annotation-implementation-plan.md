# Spoken Translation And Unit Annotation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make spoken-language translation the default and add deterministic numeric, unit, roughness, and abrasive-size annotations to final translation outputs without regressing existing translation, retranslation, assist, or minutes flows.

**Architecture:** Keep LLM work responsible only for spoken-style translation and move all numeric enrichment into a new pure post-processing layer. Apply that layer exactly once on final translation outputs in `TranslatorApp._poll_queue()` and on retranslation outputs in `RetranslationWorker._execute_retranslation()`, while explicitly excluding `transcript`, assist, and minutes events.

**Tech Stack:** Python 3.13, tkinter/ttk, pytest, unittest.mock, existing realtime translator workers

---

## File Map

- Create: `realtime_translator/translation_postprocess.py`
  - Pure post-processing API for engineering-unit conversion, abrasive lookup, roughness annotation, and English number readings
- Create: `realtime_translator/unit_tables.py`
  - Fixed lookup tables and documented conversion constants for abrasive-size mappings and engineering-unit coefficients
- Modify: `realtime_translator/prompts.py`
  - Spoken-style instructions for normal translation, 2-phase translation, and retranslation prompts
- Modify: `realtime_translator/app.py`
  - Apply post-processing exactly once in `translation_done` handling, while excluding `transcript`, assist, and minutes flows
- Modify: `realtime_translator/retranslation.py`
  - Apply the same post-processing to retranslation results before `retrans_result`
- Test: `tests/test_prompts.py`
  - Prompt contract tests for spoken-style defaults and number-preservation wording
- Create: `tests/test_translation_postprocess.py`
  - Pure-function tests for unit conversion, abrasive mappings, number reading, ambiguity handling, rounding, and failure fallbacks
- Modify: `tests/test_api.py`
  - Lock the contract that upstream translation workers still emit raw `translation_done` payloads
- Modify: `tests/test_openai_llm.py`
  - Lock the same raw-output contract for OpenAI/OpenRouter LLM workers
- Modify: `tests/test_retranslation.py`
  - Ensure retranslation path applies post-processing exactly once
- Modify: `tests/test_integration.py`
  - Verify both legacy 5-field and auto 8-field `translation_done` payloads, `output_language` resolution, transcript exclusion, assist/minutes routing, fallback behavior, and that annotated final text is what reaches history through `TranslatorApp._poll_queue()`
- Leave: `tests/test_history.py`
  - No Task 3 change unless the `TranslationHistory` data contract itself changes; annotated-history persistence is locked by the integration suite

## Decisions Frozen Before Implementation

- Spoken-language translation is the default for all translation prompts, not a user-facing toggle
- Post-processing runs only on final translations, never on `transcript` events
- `annotate_translation(text: str, *, output_language: str) -> str` is the only public API for the post-processing module
- Pressure annotations may show dual metric outputs only when the source unit is `psi`
- Temperature rounds to one decimal place by default
- Tolerance values may exceed the normal rounding cap if needed to avoid losing information
- `grit` without an explicit standard marker remains unannotated

## Verification Ladder

- Narrow tests after each task:
  - `pytest tests/test_prompts.py tests/test_translation_postprocess.py -q`
  - `pytest tests/test_retranslation.py tests/test_integration.py -q`
- Final regression:
  - `pytest -q`

### Task 1: Lock The Prompt Contract For Spoken Translation

**Files:**
- Modify: `realtime_translator/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write failing prompt contract tests**

```python
def test_build_prompt_requests_spoken_translation():
    prompt = build_prompt("listen_en_ja", "factory meeting", show_original=False)
    assert "spoken" in prompt.lower()
    assert "natural" in prompt.lower()
    assert "negation" in prompt.lower()
    assert "conditions" in prompt.lower()


def test_build_translation_prompt_preserves_numbers_and_conditions():
    prompt = build_translation_prompt("listen_en_ja", "factory meeting", "torque is 10 Nm")
    assert "do not change" in prompt.lower()
    assert "numbers" in prompt.lower()
    assert "units" in prompt.lower()
    assert "deadlines" in prompt.lower()


def test_build_retranslation_prompt_requests_spoken_translation():
    prompt = build_retranslation_prompt("listen_en_ja", "factory meeting", ">>> hello")
    assert "spoken" in prompt.lower()
    assert "natural" in prompt.lower()


def test_auto_direction_prompt_preserves_numbers_and_conditions():
    prompt = build_prompt("listen_auto", "factory meeting", show_original=False)
    assert "numbers" in prompt.lower()
    assert "units" in prompt.lower()
```

- [ ] **Step 2: Run the targeted prompt tests to confirm they fail**

Run: `pytest tests/test_prompts.py -q`
Expected: FAIL because the new spoken-style requirements are not present yet

- [ ] **Step 3: Update the three translation prompt builders with the minimal wording needed**

```python
"Translate into natural spoken language suitable for live interpretation.\n"
"Do not weaken or alter numbers, units, negation, conditions, quantities, or deadlines.\n"
```

- [ ] **Step 4: Keep reply-assist and minutes prompts unchanged**

Run: only touch `build_prompt(...)`, `build_translation_prompt(...)`, and `build_retranslation_prompt(...)`

- [ ] **Step 5: Run the prompt tests again**

Run: `pytest tests/test_prompts.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add realtime_translator/prompts.py tests/test_prompts.py
git commit -m "feat: default translation prompts to spoken style"
```

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


def test_mesh_lookup_uses_fixed_table_value():
    assert "micron" in annotate_translation("100 mesh", output_language="en")


def test_partial_failure_returns_original_text():
    assert annotate_translation("12 mm", output_language="en") == "12 mm"


def test_api_worker_translation_done_payload_stays_raw_text():
    done = [m for m in messages if m[0] == "translation_done"][0]
    assert done[6] == "こんにちは"


def test_openai_llm_translation_done_payload_stays_raw_text():
    done = [m for m in messages if m[0] == "translation_done"][0]
    assert done[6] == "Hello"
```

- [ ] **Step 2: Run the targeted post-processing and upstream contract tests to confirm they fail**

Run: `pytest tests/test_translation_postprocess.py tests/test_api.py tests/test_openai_llm.py -q`
Expected: FAIL because the module does not exist yet and the raw-output contract tests are not present yet

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

- [ ] **Step 5: Cover the required behaviors before widening scope**

Run tests for:
- engineering units: `mm / cm / m / in / ft`, `g / kg / lb`, `C / F`, `Nm / lbf·ft`, `MPa / bar / psi`
- `Ra`
- `JIS # / FEPA P / mesh / micron`
- ambiguity exclusions: identifiers, dates, versions, bare `grit`
- partial failure fallback
- `realtime_translator/api.py` and `realtime_translator/openai_llm.py` continue to emit raw `translation_done` text and do not import the post-processing layer

- [ ] **Step 6: Run the focused pure-function suite**

Run: `pytest tests/test_translation_postprocess.py tests/test_api.py tests/test_openai_llm.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/unit_tables.py realtime_translator/translation_postprocess.py tests/test_translation_postprocess.py tests/test_api.py tests/test_openai_llm.py
git commit -m "feat: add deterministic translation annotation pipeline"
```

### Task 3: Integrate Post-Processing Into Final Translation Events Only

**Files:**
- Modify: `realtime_translator/app.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing integration tests that lock both `translation_done` shapes, annotated history persistence, and fallback behavior**

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


def test_translation_done_annotation_failure_falls_back_to_raw(mocker):
    from realtime_translator.app import TranslatorApp

    app = _make_poll_queue_app()
    mocker.patch("realtime_translator.app.annotate_translation", side_effect=RuntimeError("boom"))
    app._ui_queue.put(("translation_done", "listen_en_ja", "12:00:00", "12 mm", "12 mm"))
    TranslatorApp._poll_queue(app)

    entry = app._controller.history.all_entries()[-1]
    assert entry.translation == "12 mm"


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

Run: `pytest tests/test_integration.py -k "translation_done or transcript or fallback" -q`
Expected: FAIL because `app.py` still appends raw `translation` and does not resolve `output_language` for post-processing

- [ ] **Step 3: Resolve `output_language` from the final direction and annotate before history append, with raw-text fallback on exception**

```python
if resolved_direction == "en_ja":
    resolved_output_language = "ja"
elif resolved_direction == "ja_en":
    resolved_output_language = "en"
else:
    resolved_output_language = "ja" if mode == "en_ja" else "en"

try:
    annotated_translation = annotate_translation(
        translation,
        output_language=resolved_output_language,
    )
except Exception:
    logging.exception("translation annotation failed; keeping raw translation")
    annotated_translation = translation
```

- [ ] **Step 4: Preserve the routing boundary explicitly**

Run: keep `_on_transcript(...)`, `assist_result`, `assist_error`, and minutes handling unchanged, and continue accepting both the legacy 5-field and auto 8-field `translation_done` event shapes

- [ ] **Step 5: Treat annotated history persistence as part of the integration contract**

Run: keep the history assertions in `tests/test_integration.py`; do not replace them with call-count-only regressions

- [ ] **Step 6: Run focused regressions**

Run: `pytest tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add realtime_translator/app.py tests/test_integration.py
git commit -m "feat: annotate final translation outputs"
```

### Task 4: Integrate Retranslation Without Touching Assist Or Minutes

**Files:**
- Modify: `realtime_translator/retranslation.py`
- Modify: `tests/test_retranslation.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing retranslation and assist/minutes routing tests**

```python
def test_retranslation_result_is_annotated_once(mocker, worker):
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


def test_assist_result_routes_to_on_assist_result_without_annotation(app, mocker):
    annotate_spy = mocker.patch("realtime_translator.app.annotate_translation")
    assist_spy = mocker.patch.object(app._tools_panel, "on_assist_result")
    app._ui_queue.put(("assist_result", "req1", "reply_assist", "hello"))
    app._poll_queue()
    annotate_spy.assert_not_called()
    assist_spy.assert_called_once_with("req1", "hello")


def test_minutes_result_routes_to_on_minutes_result_without_annotation(app, mocker):
    annotate_spy = mocker.patch("realtime_translator.app.annotate_translation")
    minutes_spy = mocker.patch.object(app._tools_panel, "on_minutes_result")
    app._ui_queue.put(("assist_result", "req2", "minutes", "hello"))
    app._poll_queue()
    annotate_spy.assert_not_called()
    minutes_spy.assert_called_once_with("req2", "hello")
```

- [ ] **Step 2: Run the targeted tests to confirm they fail**

Run: `pytest tests/test_retranslation.py tests/test_integration.py -k "annotat or fallback or assist_result_routes_to_on_assist_result_without_annotation or minutes_result_routes_to_on_minutes_result_without_annotation" -q`
Expected: FAIL because retranslation still returns raw text and the assist/minutes routing contract is not yet locked by tests

- [ ] **Step 3: Apply post-processing inside `_execute_retranslation(...)`, with raw-text fallback on exception**

```python
result = self._call_gemini(client, prompt)
try:
    return annotate_translation(result, output_language=dst_language)
except Exception:
    logging.exception("retranslation annotation failed; keeping raw translation")
    return result
```

- [ ] **Step 4: Keep the assist/minutes dispatch contract unchanged while excluding annotation**

Run: preserve the existing `TranslatorApp._poll_queue()` branches that call `self._tools_panel.on_assist_result(request_id, text)` and `self._tools_panel.on_minutes_result(request_id, text)` as-is, and do not import or call the post-processing layer from `assist.py`, `workspace_panel.py`, `api.py`, or `openai_llm.py`

- [ ] **Step 5: Run focused regressions**

Run: `pytest tests/test_retranslation.py tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add realtime_translator/retranslation.py tests/test_retranslation.py tests/test_integration.py
git commit -m "feat: annotate retranslation outputs"
```

### Task 5: Lock Regression Coverage And Run Full Verification

**Files:**
- Modify: `tests/test_translation_postprocess.py`
- Modify: `tests/test_integration.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Add the last missing regression cases before the full run**

Run tests for:
- `psi -> MPa / bar` dual annotation only when source is `psi`
- temperature rounding stays at one decimal place
- `Phase 1 transcript` remains unannotated in 2-phase mode
- precision-preserving tolerance conversion
- `grit` without standard marker remains unchanged
- non-ASCII numeric forms (`１２`, `−`, decimal comma, full-width punctuation`) fail safe or annotate correctly by documented rules
- `output_language` resolution fallback in unexpected direction cases remains deterministic

- [ ] **Step 2: Run the narrow suites**

Run: `pytest tests/test_prompts.py tests/test_translation_postprocess.py tests/test_retranslation.py tests/test_integration.py -q`
Expected: PASS

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_prompts.py tests/test_translation_postprocess.py tests/test_retranslation.py tests/test_integration.py
git commit -m "test: lock translation annotation regressions"
```

## Manual Smoke Checklist

- [ ] 通常翻訳で訳文が書き言葉より話し言葉寄りになっている
- [ ] `12 mm` のような寸法で、訳文に括弧付き補足が付く
- [ ] 英語出力では数値の読み方が付く
- [ ] `M8`, `A356`, `2026-03-20`, `v2.1.0` のような識別子や日付に誤注釈しない
- [ ] `#400`, `P400`, `mesh`, `Ra 0.8` の補足が出る
- [ ] 2フェーズの中間 transcript には注釈が出ず、最終訳だけに出る
- [ ] 再翻訳結果に同じ後処理が適用される
- [ ] 返答アシストの結果は注釈されず、従来どおりアシスト欄に表示される
- [ ] 議事録の結果は注釈されず、従来どおり議事録欄に反映される

## Final Verification Commands

```bash
pytest tests/test_prompts.py tests/test_translation_postprocess.py -q
pytest tests/test_retranslation.py tests/test_integration.py -q
pytest -q
```

## Handoff Notes

- Keep the post-processing layer pure; do not let it reach into UI state or history internals.
- Prefer exact token classification rules over heuristic expansion when ambiguity is high.
- If annotated translations are persisted into history, verify downstream consumers such as retranslation inputs, export, and comparison flows after each task boundary instead of waiting for the final suite.
- Keep prompt changes and post-processing rollout isolated by task/commit so either half can be reverted independently if regressions appear.
- If a spec mismatch is discovered during Task 5, update the spec in a separate commit before changing behavior further.

## Codex Review Notes
*(auto-appended by codex-review - 2026-03-20 15:26)*

### Review Lenses

- Lens A: 仕様整合とスコープ境界
- Lens B: 実装順序と統合ポイントの安全性
- Lens C: テスト戦略と回帰防止

### Incorporated Feedback

- [High] `translation_done` の legacy 5-field / auto 8-field 両経路、`output_language` 解決、annotated-history persistence を統合テストへ固定
- [High] `api.py` / `openai_llm.py` の upstream raw-output 契約を Task 2 のテスト対象へ追加
- [High] annotation 失敗時に raw translation を保持する fallback を Task 3/4 の実装例と回帰テストへ追加
- [High] assist/minutes は「annotateしない」だけでなく既存ハンドラへ従来どおり届くことを回帰テストと manual smoke に追加
- [Medium] prompt テストを `build_retranslation_prompt()` と auto-direction パスまで拡張
- [Medium] Task 5 から spec 編集を外し、non-ASCII 数値系と `output_language` fallback の回帰ケースを追加
- [Medium] annotated-history の downstream 影響と rollback 分離を Handoff Notes に追加

### Skipped Feedback

- hidden risk の「観測用カウンタ追加」: 実装スコープを広げるため今回は計画へは入れず、`logging.exception` ベースの最小観測に留めた
- hidden risk の「table 更新責任者」: 実装計画より運用ルール寄りのため今回は反映しない
