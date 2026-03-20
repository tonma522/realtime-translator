## Plan Review

**Status:** Approved

**Issues (if any):**

- [Task 5, Step 3]: `shell.columnconfigure(2, minsize=workspace_min_width)` のコードスニペットでカラム 0 (左カラム) の `minsize` 指定が抜けている。実装時に左カラムが縮みすぎる可能性がある。

- [Task 6, Step 4]: 「PTT keyboard handling remains intact after layout swap」がステップの箇条書きとして記載されているが、対応するテストコードスニペットが Task 6 Step 1 の failing test 群に含まれていない。PTT はレイアウト差し替えで既存ショートカットバインドが外れやすい箇所なので、明示的に failing test として先に書く必要がある。

**Recommendations (advisory, do not block approval):**

- `ToolsPanel` の compatibility shim 戦略 (Task 2 Step 4) について「keeping compatibility にするか one-pass で置き換えるか」を decision point として残しているが、既存 `tests/test_tools_panel.py` に shim 前提のテストが混入すると Task 6 で二度手間になる。Task 2 のコミット前に方針を one-pass 寄せに倒すか、shim に明示的な deprecation コメントを入れておくと後続タスクがスムーズ。

- 未解決事項 3 点 (左サマリー粒度・ブロッカー表示形式・最小ウィンドウ幅) は Plan の "Pre-Implementation Decisions To Freeze" で回答済みだが、spec の完了条件「実装着手前に確定され、その結果が実装計画へ反映されている」との対応を明示するコメントを Task 1 Step 1 の前に一行入れると reviewerへの伝達が楽になる。

- `tests/test_config.py` が Task 4 Step 6 の検証コマンドに含まれているが、File Map に `config.py` の修正は "only if needed" と書かれており、実際に修正しない場合はテスト対象として不要になる。実装開始前に確認しておくと混乱を避けられる。
