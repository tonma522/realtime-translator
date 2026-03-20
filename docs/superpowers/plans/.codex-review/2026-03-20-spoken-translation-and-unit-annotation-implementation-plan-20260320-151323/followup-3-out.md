Section: Task 4: Integrate Retranslation Without Touching Assist Or Minutes
Replace:
`- [ ] **Step 1: Write failing retranslation tests**` から `- [ ] **Step 5: Run focused regressions**` までの、`test_assist_and_minutes_paths_do_not_call_annotation(...)` を含む回帰防止ブロック
With:
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

Run: `pytest tests/test_retranslation.py tests/test_integration.py -k "retranslation_result_is_annotated_once or assist_result_routes_to_on_assist_result_without_annotation or minutes_result_routes_to_on_minutes_result_without_annotation" -q`  
Expected: FAIL because retranslation still returns raw text and the assist/minutes routing contract is not yet locked by tests

- [ ] **Step 3: Apply post-processing inside `_execute_retranslation(...)`**

```python
result = self._call_gemini(client, prompt)
return annotate_translation(result, output_language=dst_language)
```

- [ ] **Step 4: Keep the assist/minutes dispatch contract unchanged while excluding annotation**

Run: preserve the existing `TranslatorApp._poll_queue()` branches that call `self._tools_panel.on_assist_result(request_id, text)` and `self._tools_panel.on_minutes_result(request_id, text)` as-is, and do not import or call the post-processing layer from `assist.py` or `workspace_panel.py`

- [ ] **Step 5: Run focused regressions**

Run: `pytest tests/test_retranslation.py tests/test_integration.py -q`  
Expected: PASS
Reason: 「annotate を呼ばない」だけでは無注釈化しか固定できず、既存の assist / minutes 結果が従来どおり UI ハンドラへ到達することを保証できないため。呼び先と引数まで回帰テストで固定する必要があります。

Section: Manual Smoke Checklist
Replace:
- [ ] 返答アシストと議事録の結果は注釈されない
With:
- [ ] 返答アシストの結果は注釈されず、従来どおりアシスト欄に表示される
- [ ] 議事録の結果は注釈されず、従来どおり議事録欄に反映される
Reason: 手動確認でも「注釈されない」だけでなく、「既存の表示先に届く」ことまで確認できるようにするため。