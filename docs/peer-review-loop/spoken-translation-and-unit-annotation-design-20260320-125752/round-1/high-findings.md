# High Findings Summary

- [High] 後処理の統合ポイントが曖昧で、通常翻訳・2フェーズ翻訳・再翻訳のどこで `annotate_translation(...)` を呼ぶかが未定義だった
- [High] 完了条件が欠けており、実装完了の判定と検証ゲートが曖昧だった
- [High] 英語出力の裸数字読み付与が広すぎ、型番・日付・バージョン番号との境界が未定義だった

修正方針:

- `app.py`, `api.py`, `openai_llm.py`, `retranslation.py` の統合ポイントを spec に明記する
- `Done Criteria` を追加し、テスト通過と適用経路を完了条件に固定する
- 数字読みの除外対象と判定原則を追加する
