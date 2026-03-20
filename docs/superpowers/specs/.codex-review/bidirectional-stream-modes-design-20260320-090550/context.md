## Review Context
- Plan type: 設計計画書（双方向ソース別翻訳モード、仮想ストリーム方式）
- Repo rules: AGENTS.md=NOT_FOUND, CLAUDE.md=NOT_FOUND, TODO.md=NOT_FOUND。セッション制約として日本語回答、Windowsパス表記、スキル利用必須
- Stack: Python 3.11、pytest、tkinter UI、OpenAI/Gemini/Whisper/webrtcvad オプション対応
- Relevant recent changes: 直近は settings window refactor、response speedup、worker queue fix、multi-backend support
- Test signals: pytest testpaths=tests。未実行。git diff --stat は空、working tree は docs/ と複数 plan ファイルが未追跡
- Key constraints: 実装はまだ行わない。設計書のみ更新対象。速度優先。auto モードで英日/日英を自動判定しつつ固定方向に近い体感速度を維持したい
