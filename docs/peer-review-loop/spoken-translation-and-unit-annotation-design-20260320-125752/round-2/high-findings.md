# High Findings Summary

- [High] 返答アシストと議事録を後処理対象から除外する契約が弱く、イベント種別ベースの除外方法が未定義だった
- [High] `annotate_translation(...)` の公開 API が広すぎ、stream 系引数の目的が不明だった
- [High] 二重適用禁止の要件はあったが、保証方法と検証方法が未定義だった

修正方針:

- `annotate_translation(...)` を `output_language` だけ受ける raw translation 専用 API に絞る
- `assist_result` / 議事録系は `translation_done` を発火しない契約を spec に追加する
- 二重適用は呼び出し側契約で防ぎ、call count を integration test で固定すると明記する
