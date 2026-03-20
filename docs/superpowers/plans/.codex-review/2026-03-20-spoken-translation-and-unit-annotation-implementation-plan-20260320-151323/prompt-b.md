以下の実装計画書を Codex としてレビューしてください。回答は日本語で、質問はせず、各行の先頭に `[High]` `[Medium]` `[Low]` のいずれかを付けてください。問題がなければ `[Low] 大きな問題なし` のように短くまとめてください。

レビュー対象ファイル:
- `docs/superpowers/plans/2026-03-20-spoken-translation-and-unit-annotation-implementation-plan.md`

関連 spec:
- `docs/superpowers/specs/2026-03-20-spoken-translation-and-unit-annotation-design.md`

レビューコンテキスト:
- 実装計画書レビュー
- Python 3.13 / tkinter / pytest
- UI リファクタ済みで `app.py`, `workspace_panel.py`, `translation_timeline_panel.py` などが存在する
- 後処理は `app.py` の final translation 経路と `retranslation.py` のみで統合予定
- 計画書だけを更新対象とし、実装自体は行わない

今回のレンズ:
- 実装順序と統合ポイントの安全性

重点観点:
- タスク順序が安全で、途中段階でも壊れにくいか
- `app.py`, `retranslation.py`, `prompts.py`, 新規後処理モジュールの責務分割が妥当か
- 既存 UI / assist / minutes / history を壊すリスクが plan に対処されているか
- コミット粒度と検証順序が実務的か
