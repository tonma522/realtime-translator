# High Findings Summary
- [High] `resolve_virtual_stream_id` の unknown stream_id で KeyError になるリスク
- [High] `direction_parse_failed` 後のフォールバック動作と UI 扱いが未定義
- [High] Task 4 の `api.py` / `openai_stt.py` / `whisper_stt.py` の最小実装が空白で、イベント契約が不明
- [High] `usable_for_downstream` が `direction_parse_failed` 以外のエラーを通してしまう
