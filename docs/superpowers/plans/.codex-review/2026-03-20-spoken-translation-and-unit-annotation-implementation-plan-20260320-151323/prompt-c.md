以下の実装計画書を Codex としてレビューしてください。回答は日本語で、質問はせず、各行の先頭に `[High]` `[Medium]` `[Low]` のいずれかを付けてください。問題がなければ `[Low] 大きな問題なし` のように短くまとめてください。

レビュー対象ファイル:
- `docs/superpowers/plans/2026-03-20-spoken-translation-and-unit-annotation-implementation-plan.md`

関連 spec:
- `docs/superpowers/specs/2026-03-20-spoken-translation-and-unit-annotation-design.md`

レビューコンテキスト:
- 実装計画書レビュー
- Python 3.13 / tkinter / pytest
- 既に `tests/test_prompts.py`, `tests/test_retranslation.py`, `tests/test_integration.py`, `tests/test_history.py` が存在する
- 新規で pure-function の postprocess test を追加予定
- 計画書だけを更新対象とし、実装自体は行わない

今回のレンズ:
- テスト戦略と回帰防止

重点観点:
- failing test -> implementation -> verification の流れが十分に具体的か
- 重要な regression path が漏れていないか
- flaky になりやすい test や過剰に壊れやすい assertion を誘発していないか
- full suite 前の絞り込み検証が妥当か
