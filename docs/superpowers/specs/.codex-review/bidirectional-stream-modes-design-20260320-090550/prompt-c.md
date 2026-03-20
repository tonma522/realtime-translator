以下の設計書を日本語でレビューしてください。
対象ファイル: docs/superpowers/specs/2026-03-20-bidirectional-stream-modes-design.md

## Review Context
- Plan type: 設計計画書（双方向ソース別翻訳モード、仮想ストリーム方式）
- Repo rules: AGENTS.md=NOT_FOUND, CLAUDE.md=NOT_FOUND, TODO.md=NOT_FOUND。セッション制約として日本語回答、Windowsパス表記、スキル利用必須
- Stack: Python 3.11、pytest、tkinter UI、OpenAI/Gemini/Whisper/webrtcvad オプション対応
- Relevant recent changes: 直近は settings window refactor、response speedup、worker queue fix、multi-backend support
- Test signals: pytest testpaths=tests。未実行。git diff --stat は空、working tree は docs/ と複数 plan ファイルが未追跡
- Key constraints: 実装はまだ行わない。設計書のみ更新対象。速度優先。auto モードで英日/日英を自動判定しつつ固定方向に近い体感速度を維持したい


出力ルール:
- 指摘は1行ずつ [High] [Medium] [Low] で始める
- 質問はしない
- そのレンズにのみ集中する
- 具体的にどの仕様が不足・矛盾・過剰かを書く
- 問題がなければ [Low] 問題なし と書いてよい
レビュー観点: テスト完備性・完了条件・段階的実装順序
特に見る点:
- 完了条件が十分に検証可能か
- テスト方針に抜けがないか
- 段階的実装順序が中間不整合を生まないか
