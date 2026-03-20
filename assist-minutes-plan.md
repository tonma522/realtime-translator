# Plan: 返答アシスト + 議事録生成

**Generated**: 2026-03-08
**Estimated Complexity**: Medium

## Overview

リアルタイム翻訳アプリに2つのLLMアシスト機能を追加する:
- **返答アシスト**: 直近の会話履歴+コンテキストから返答候補を3つ提案（日本語+英語訳併記）
- **議事録生成**: 翻訳履歴を時系列で要約し、差分追記型の議事録を生成

両機能とも手動トリガー（ボタン）、低優先度（idle時のみ実行）、既存の `RetranslationWorker` パターンを踏襲。
1つのワーカー `AssistWorker` でアシスト・議事録の両リクエストを処理し、アシスト優先でキュー管理する。

## Prerequisites
- Sprint 1-4 完了済み（翻訳履歴、再翻訳ワーカー、idle検出、適応ポーリング）
- `TranslationHistory` に十分なエントリが蓄積されていること（空の場合はエラーメッセージ）

---

## Sprint 1: AssistWorker + プロンプト + Controller統合 + UIキュー単一消費者化

**Goal**: バックエンドのワーカー・プロンプト・Controller統合を完成させ、UIなしでテスト可能にしつつ、UIイベント配送を単一消費者に整理して Assist/Minutes/Re-translation 系ダイアログを安全に扱えるようにする

**Demo/Validation**:
- `python -m pytest tests/test_assist.py -v` — 全テストパス
- `python -m pytest tests/ -q` — 既存テスト含め全パス

### Task 1.1: プロンプト関数を追加
- **Location**: `realtime_translator/prompts.py`
- **Description**: 2つのプロンプトビルダーを追加
  - `build_reply_assist_prompt(context, history_block) -> str`
    - 直近の会話履歴を渡し、3つの返答候補を提案させる
    - 各候補は「日本語の返答案」+「英語訳」を併記
    - 出力フォーマット指定:
      ```
      1. [日本語] ...
         [English] ...
      2. [日本語] ...
         [English] ...
      3. [日本語] ...
         [English] ...
      ```
  - `build_minutes_prompt(context, history_block, previous_minutes) -> str`
    - 会話履歴を渡し、時系列の議事録要約を日本語で生成
    - `previous_minutes` が空でなければ「前回の議事録に追記」指示
    - 出力: 日本語の議事録テキスト
- **Dependencies**: なし
- **Acceptance Criteria**:
  - 両関数がコンテキスト・履歴ブロックを含むプロンプト文字列を返す
  - `build_minutes_prompt` は `previous_minutes` が空のとき「新規作成」、非空のとき「追記」指示を含む
- **Validation**: `tests/test_prompts.py` に追加テスト

### Task 1.2: AssistWorker を実装（idle判定明文化 + 優先キュー + 履歴上限）
- **Location**: `realtime_translator/assist.py` (NEW)
- **Description**: `RetranslationWorker` を参考に `AssistWorker` を実装。idle判定は Assist 専用仕様として明文化する
  - `@dataclass AssistRequest`:
    - `request_id: str` — uuid hex[:8]
    - `request_type: str` — `"reply_assist"` | `"minutes"`
    - `context: str`
    - `n_history: int` — 参照する直近の履歴件数（アシスト用、デフォルト20）
    - `previous_minutes: str` — 前回の議事録テキスト（議事録用）
    - `priority: int` — 0=reply_assist（高）, 1=minutes（低）
    - `seq: int` — 同一優先度内のFIFO保証用シーケンス番号
    - `__lt__` を実装: `(self.priority, self.seq) < (other.priority, other.seq)`
  - `AssistWorker` クラス:
    - コンストラクタ: `ui_queue, history, monitored_workers, llm_backend, model, api_key, min_interval_sec=8.0, client_factory=None`
    - ライフサイクル: `start/submit/signal_stop/join/stop`（RetranslationWorker と同じ）
    - `submit(request_type, context, n_history=20, previous_minutes="") -> str` — request_id を返す
    - `_is_idle() -> bool` — Assist の実行条件を単一メソッドに集約
      - 仕様: `monitored_workers` の全 worker が `pending_requests == 0` かつ `is_busy == False` のときのみ idle
      - `monitored_workers` には LLM API worker + STT worker (Whisper/OpenAI STT) を含める
      - `AssistWorker` 自身は監視対象に含めない
    - `_worker_loop`: `_is_idle()` に基づく待機 + レート制限
      - `queue.PriorityQueue` を使用し、`reply_assist` を `minutes` より優先。同一優先度はFIFO保証
    - `_execute_reply_assist(req, client) -> str`: 直近 `n_history` 件の履歴を整形 → プロンプト生成 → API呼び出し
    - `_execute_minutes(req, client) -> str`: 全履歴を整形 → プロンプト生成 → API呼び出し
      - **履歴上限**: 議事録用は直近200件 or 合計文字数50,000字で切り詰め（トークン予算保護）
      - `previous_minutes` を含めた合計が上限を超える場合、古い履歴を省略し「（省略）」を付与
    - `_format_history_for_assist(entries) -> str`: 会話履歴の整形（方向+原文+訳文）
    - `_format_history_for_minutes(entries) -> str`: タイムスタンプ付き会話履歴
    - `_call_gemini` / `_call_openai`: RetranslationWorker と同じパターン
      - **空応答ガード**: 空文字列の場合は `assist_error` を送信（blank success 防止）
  - UIキューメッセージ:
    - `("assist_result", request_id, request_type, text)`
    - `("assist_error", request_id, request_type, error_message)`
- **Dependencies**: Task 1.1
- **Acceptance Criteria**:
  - アシスト・議事録の両リクエストを処理できる
  - idle 条件がコード上の単一メソッド `_is_idle()` で定義されている
  - STT worker が busy または pending の間は Assist が実行開始しない
  - `reply_assist` が `minutes` より優先され、同一優先度はFIFO
  - 長い履歴が切り詰められる
  - 空応答時に `assist_error` が送信される
- **Validation**: `tests/test_assist.py` (NEW)

### Task 1.3: Controller に AssistWorker を統合（factory注入パターン）
- **Location**: `realtime_translator/controller.py`, `tests/test_controller.py`
- **Description**:
  - `AssistWorkerFactory = Callable[..., AssistWorker]` 型エイリアスを追加
  - `TranslatorController.__init__` に `assist_worker_factory` パラメータを追加（既存の worker factory 注入パターンに準拠）
  - `_assist_worker: AssistWorker | None` 属性を追加
  - `_start_workers()` で `AssistWorker` を生成・開始
    - `monitored_workers` には LLM API worker に加え、Whisper/OpenAI STT worker も含める
  - `stop()` で `_assist_worker` を停止。停止時に pending な assist リクエストはキャンセル扱い
  - 新メソッド:
    - `request_reply_assist(n_history: int = 20) -> str` — request_id を返す
    - `request_minutes(previous_minutes: str = "") -> str` — request_id を返す
    - `can_assist() -> bool` — `self._running and len(self._history.all_entries()) > 0`
  - worker リストに `_assist_worker` 自体は含めない（自身のidle待ちでデッドロック防止）
  - `tests/test_controller.py` に `FakeAssistWorker` を追加し、factory 注入でテスト
- **Dependencies**: Task 1.2
- **Acceptance Criteria**:
  - `request_reply_assist()` / `request_minutes()` が request_id を返す
  - `stop()` で assist_worker も正常停止
  - 履歴が空の場合は空文字列を返す
  - `FakeAssistWorker` を使ったテストが `test_controller.py` に存在する
- **Validation**: `tests/test_controller.py` に追加テスト

### Task 1.4: UIキューの単一消費者化と result store 導入
- **Location**: `realtime_translator/app.py`
- **Description**:
  - `ui_queue` の `get_nowait()` は `app.py` のメイン `_poll_queue()` のみに限定し、各ダイアログは `ui_queue` を直接読まない構造へ変更する
  - `app.py` に `_result_store: dict[str, tuple]` を追加。`request_id` / `batch_id` をキーに結果を保持
  - `_poll_queue()` で `assist_result` / `assist_error` / `retrans_result` / `retrans_error` を受信したら `_result_store` に格納
  - ダイアログは `request_id` を使って `_result_store` をポーリング（`root.after` ベース）
  - 既存の `RetranslationDialog` も `_result_store` 経由に移行し、requeue パターンを廃止
  - stop 時: `_poll_queue` が drain される前に `_result_store` に残存する pending request を cancelled 扱いにする
- **Dependencies**: Task 1.2, 1.3
- **Acceptance Criteria**:
  - `ui_queue` を直接消費する UI コンポーネントは `_poll_queue()` だけになる
  - 複数ダイアログが同時に開いていても、結果が別 request に誤配送されない
  - requeue ロジックが不要になる
  - stop 時にダイアログが永遠に待機状態にならない

### Task 1.5: テスト追加
- **Location**: `tests/test_assist.py` (NEW), `tests/test_prompts.py`, `tests/test_controller.py`
- **Description**:
  - `test_assist.py`:
    - `test_submit_returns_request_id` — submit が8文字hex IDを返す
    - `test_reply_assist_execution` — モッククライアントでアシスト実行 → UIキューに結果
    - `test_minutes_execution` — モッククライアントで議事録実行 → UIキューに結果
    - `test_minutes_with_previous` — previous_minutes ありでプロンプトに含まれる
    - `test_idle_check_blocks_when_busy` — ワーカーbusy時は実行しない
    - `test_idle_includes_stt_workers` — STT worker busy で idle=False
    - `test_priority_assist_over_minutes` — アシストが議事録より先に実行される
    - `test_same_priority_fifo` — 同一優先度のリクエストがFIFO順で処理される
    - `test_stop_lifecycle` — start → stop で正常終了
    - `test_empty_history_error` — 履歴空でエラーメッセージ
    - `test_history_truncation` — 長い履歴が切り詰められる
    - `test_empty_response_becomes_error` — 空応答で assist_error が返る
  - `test_prompts.py`:
    - `test_build_reply_assist_prompt` — コンテキスト・履歴を含む
    - `test_build_minutes_prompt_new` — 新規議事録プロンプト
    - `test_build_minutes_prompt_append` — 追記議事録プロンプト
  - `test_controller.py`:
    - `test_can_assist_requires_history` — 履歴なしで False
    - `test_request_reply_assist` — request_id が返る
    - `test_assist_worker_factory_injection` — factory 注入で FakeAssistWorker が使われる
- **Dependencies**: Task 1.1, 1.2, 1.3, 1.4

---

## Sprint 2: UI — 返答アシストダイアログ

**Goal**: 返答アシストのボタンとダイアログを実装し、エンドツーエンドで動作確認

**Demo/Validation**:
- アプリ起動 → 翻訳開始 → 会話蓄積 → 「返答アシスト」ボタン → ダイアログ表示 → 3つの返答候補表示
- `python -m pytest tests/ -q` — 全テストパス（自動テスト）
- 手動UI確認: ダイアログ表示・操作・多重起動防止

### Task 2.1: メインウィンドウに「返答アシスト」ボタン追加
- **Location**: `realtime_translator/app.py` — `_build_ui` の `btn_frame` 付近
- **Description**:
  - 「再翻訳...」ボタンの隣に「返答アシスト」ボタンを追加
  - 翻訳中のみ有効化（`can_assist()` で制御）
  - クリックで `ReplyAssistDialog` を開く
  - **多重起動防止**: 既存ウィンドウがあれば `focus_set()` で前面に出す
- **Dependencies**: Sprint 1 完了
- **Acceptance Criteria**:
  - ボタンが表示され、翻訳中かつ履歴ありの場合のみ有効
  - 同じダイアログを複数開かない

### Task 2.2: ReplyAssistDialog を実装
- **Location**: `realtime_translator/app.py`
- **Description**: `ReplyAssistDialog(tk.Toplevel)` を実装
  ```
  ┌─ 返答アシスト ─────────────────────────────────────┐
  │                                                     │
  │ [アシスト実行]              状態: 待機中            │
  │                                                     │
  │ 提案:                                               │
  │ ┌─────────────────────────────────────────────────┐ │
  │ │ 1. [日本語] リードタイムを短縮できないか確認... │ │
  │ │    [English] Could we check if the lead time... │ │
  │ │                                                 │ │
  │ │ 2. [日本語] BOM変更の影響範囲を教えてくだ...   │ │
  │ │    [English] Could you tell me the scope of... │ │
  │ │                                                 │ │
  │ │ 3. [日本語] 次回のミーティングで詳細を...      │ │
  │ │    [English] Let's discuss the details in...   │ │
  │ └─────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────┘
  ```
  - 「アシスト実行」ボタン → `controller.request_reply_assist()` を呼び出し、返却された `request_id` を保持
  - ダイアログは `ui_queue` を直接ポーリングしない
  - Sprint 1 で導入した `_result_store` から `request_id` で結果を取得（`root.after` ポーリング）
  - 結果は `ScrolledText` に表示（読み取り専用）
- **Dependencies**: Task 2.1
- **Acceptance Criteria**:
  - ダイアログが開き、実行ボタンで返答候補が表示される
  - 実行中はボタン無効化、完了/エラーで再有効化
  - ダイアログを閉じても main app のキュー処理に影響しない

---

## Sprint 3: UI — 議事録ダイアログ

**Goal**: 議事録生成のボタンとダイアログを実装し、差分追記+エクスポートが動作

**Demo/Validation**:
- アプリ起動 → 翻訳 → 「議事録」ボタン → ダイアログ → 議事録生成 → 追記 → エクスポート
- `python -m pytest tests/ -q` — 全テストパス（自動テスト）
- 手動UI確認: 議事録生成・追記・エクスポート

### Task 3.1: メインウィンドウに「議事録」ボタン追加
- **Location**: `realtime_translator/app.py` — `_build_ui` の `btn_frame`
- **Description**:
  - 「返答アシスト」ボタンの隣に「議事録」ボタン追加
  - 翻訳中かつ履歴ありの場合のみ有効
  - **多重起動防止**: 既存ウィンドウがあれば `focus_set()` で前面に出す
- **Dependencies**: Sprint 2 完了

### Task 3.2: MinutesDialog を実装
- **Location**: `realtime_translator/app.py`
- **Description**: `MinutesDialog(tk.Toplevel)` を実装
  ```
  ┌─ 議事録 ───────────────────────────────────────────┐
  │                                                     │
  │ [議事録 生成]  [エクスポート]     状態: 待機中      │
  │                                                     │
  │ 議事録:                                             │
  │ ┌─────────────────────────────────────────────────┐ │
  │ │ ## 議事録 2026-03-08                            │ │
  │ │                                                 │ │
  │ │ ### 12:30 - 12:35                               │ │
  │ │ - BOM変更の確認について議論                     │ │
  │ │ - リードタイムは3週間と確認                     │ │
  │ │                                                 │ │
  │ │ ### 12:35 - 12:40 (追記)                        │ │
  │ │ - MRPへの反映を合意                             │ │
  │ └─────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────┘
  ```
  - 「議事録 生成」ボタン → `controller.request_minutes(previous_minutes)` 呼び出し
    - `previous_minutes` は現在のテキストエリア内容を渡す（差分追記）
  - 初回は新規生成、2回目以降は既存テキストに追記
  - 「エクスポート」ボタン → `filedialog.asksaveasfilename` で `.txt` 保存
  - 結果取得: `_result_store` から `request_id` でポーリング
  - 結果は既存テキストを **置換** する（LLMが追記済みテキストを返すため）
- **Dependencies**: Task 3.1
- **Acceptance Criteria**:
  - 議事録が生成・表示される
  - 2回目以降は前回の議事録に追記される
  - エクスポートが `.txt` で保存できる

---

## Testing Strategy
- 各Sprint完了時に `python -m pytest tests/ -q` で全テストパス
- Sprint 1: ワーカー単体テスト（モッククライアント）+ プロンプト内容検証 + Controller factory 注入テスト
- Sprint 2: 自動テスト（ボタン追加） + 手動UI確認（返答アシストダイアログの表示・操作・多重起動防止）
- Sprint 3: 手動UI確認（議事録生成・追記・エクスポート）

## Potential Risks & Gotchas
1. **アシスト優先制御**: `queue.PriorityQueue` を使用し、`(priority, seq)` タプルで優先度+FIFO保証。`dataclass` に `__lt__` が必要
2. **議事録の差分追記品質**: LLMに「前回の議事録 + 新しい会話」を渡すが、長い議事録だとトークン消費が大きい。プロンプトで「変更不要な部分はそのまま出力」と指示が必要
3. **履歴件数とトークン上限**: 議事録用は直近200件 or 合計50,000字で切り詰め。`previous_minutes` を含めた合計にも上限を設ける。上限超過時はUIに通知
4. **ダイアログの多重起動**: 各ボタンに「既存ウィンドウがあれば `focus_set()`」ガードを入れる（再翻訳ダイアログも含めて統一）
5. **UIキュー単一消費者**: `_poll_queue()` のみが `ui_queue` を消費し、`_result_store` に振り分け。ダイアログは `_result_store` をポーリング。requeue パターンは完全廃止
6. **pending_requests と drop-oldest の不整合**: `enqueue_dropping_oldest()` で古い要求が捨てられてもカウンタは補正されないため、idle判定が busy 側に偏る可能性。idle判定は `queue.qsize()` ではなく `is_busy` と `pending_requests` の両方を見る設計で緩和
7. **stop 時の未配送結果消失**: `controller.stop()` が `ui_queue` を drain する際、`_result_store` への反映前の `assist_result` が消える。stop 前に result store へ pending request を cancelled として登録し、ダイアログの永久待機を防止
8. **履歴スナップショットのズレ**: Assist/Minutes は submit 時点ではなく実行時点の履歴を参照する。idle 待機中に履歴が増減すると材料がズレるが、これは「最新の会話を反映できる」利点として仕様とする。ただし `_history.clear()` 後のリクエストは空履歴でエラー
9. **空応答・安全ブロック**: LLMが空文字列やコンテンツフィルタで応答した場合、blank success にならないよう空応答を `assist_error` に変換する
10. **完了済み翻訳のみ対象**: 履歴は `translation_done` イベント時のみ追加。画面上に見えている partial/transcript は履歴に入らないため、直近の未完了発話はアシスト/議事録の材料にならない。これは仕様として明文化

## Rollback Plan
- 新規ファイル `realtime_translator/assist.py` を削除
- `prompts.py`, `controller.py`, `app.py` の追加部分を revert
- テストファイル `tests/test_assist.py` を削除
- 既存機能に影響する変更はなし（追加のみ）

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **UIキュー単一消費者化** [High]: Sprint 1 に Task 1.4 を追加。`_poll_queue()` のみが `ui_queue` を消費し、`_result_store` に振り分ける設計へ変更。ダイアログの requeue パターンを廃止。既存の `RetranslationDialog` も移行対象
- **idle 判定の明文化** [High]: Task 1.2 に `_is_idle()` メソッドを追加。`monitored_workers` に STT worker を含め、全 worker が idle のときのみ実行。仕様をコード上の単一メソッドに集約
- **Controller の factory 注入** [High]: Task 1.3 に `AssistWorkerFactory` の導入と `FakeAssistWorker` の追加を明記。既存の worker factory 注入パターンに準拠
- **アシスト優先の具体化** [Medium]: `queue.PriorityQueue` を採用し、`(priority, seq)` による優先度+FIFO保証を明記。`AssistRequest` に `__lt__` 実装
- **議事録トークン予算のタスク化** [Medium]: Task 1.2 に履歴上限（200件 / 50,000字）と切り詰めロジックを明記。テストに `test_history_truncation` 追加
- **ダイアログ多重起動のタスク化** [Medium]: Task 2.1, 3.1 に「既存ウィンドウがあれば `focus_set()`」ガードを明記
- **stop 時の結果消失** [High, Step 3]: Risks #7 に追加。stop 前に pending request を cancelled として result store に登録
- **pending_requests と drop-oldest の不整合** [High, Step 3]: Risks #6 に追加
- **空応答ガード** [Medium, Step 3]: Task 1.2 と Risks #9 に追加
- **完了済み翻訳のみ対象の明文化** [Medium, Step 3]: Risks #10 に仕様として明記

### Skipped Feedback
- **履歴スナップショットを request に保持する提案** [High, Step 3]: submit 時点のスナップショット保持は「最新の会話反映」の利点を失うため不採用。実行時点の履歴参照を仕様とし、clear 後のエラー処理で対応
- **request_id を session_id + request_id でキー化する提案** [Medium, Step 3]: 現状の `uuid4().hex[:8]` は会議1回分（1-2時間、数十リクエスト）で衝突確率は無視できる。result store は stop 時にクリアされるためセッション跨ぎの問題もない
- **Sprint 2-3 の UI テスト自動化提案** [Low]: tkinter の自動テストは投資対効果が低い。手動確認を Testing Strategy に明記して対応
