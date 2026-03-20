以下の実装計画書を Codex としてレビューしてください。回答は日本語で、質問はせず、各行の先頭に `[High]` `[Medium]` `[Low]` のいずれかを付けてください。問題がなければ `[Low] 大きな問題なし` のように短くまとめてください。

レビュー対象ファイル:
- `docs/superpowers/plans/2026-03-20-spoken-translation-and-unit-annotation-implementation-plan.md`

関連 spec:
- `docs/superpowers/specs/2026-03-20-spoken-translation-and-unit-annotation-design.md`

レビューコンテキスト:
- 実装計画書レビュー
- Python 3.13 / tkinter / pytest
- 後処理は final translation と retranslation にのみ 1 回適用する設計
- `transcript`, assist, minutes は対象外
- 計画書だけを更新対象とし、実装自体は行わない

今回のレンズ:
- 仕様整合とスコープ境界

重点観点:
- plan が spec の done criteria と整合しているか
- plan が対象外スコープを誤って巻き込んでいないか
- 公開 API, 統合ポイント, 除外経路の契約がタスクに落ちているか
- 実装中に spec 逸脱が起きる曖昧さが残っていないか
