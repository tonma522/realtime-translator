# Plan: 設定ウィンドウ分離 + メインウィンドウツール統合

**Generated**: 2026-03-08
**Estimated Complexity**: High

## Overview

現在 `app.py`（1004行）のメインウィンドウに全設定ウィジェット＋翻訳結果＋3つの別ウィンドウダイアログが混在。
これを以下のように再構成する：

1. **設定を別ウィンドウ（`SettingsWindow`）に移動** — ボタンで開閉、翻訳中も変更可能
2. **再翻訳・返答アシスト・議事録をメインウィンドウに横並びパネルとして統合** — 別ダイアログ廃止
3. **再翻訳パネルは翻訳が来るたびに最新エントリを自動選択・表示**
4. **結果表示とツールパネルを PanedWindow（7:3）で上下リサイズ可能に**

### 新ファイル構成

| ファイル | 役割 | 行数目安 |
|---------|------|---------|
| `realtime_translator/settings_window.py` | 設定ウィンドウ（Toplevel） | ~280 |
| `realtime_translator/tools_panel.py` | 再翻訳・アシスト・議事録の横並びパネル | ~300 |
| `realtime_translator/app.py` | メインウィンドウ（スリム化） | ~450 |

## Prerequisites

- 現行テストが全パス（`python -m pytest tests/ -q`）
- `app.py` の現在の構造を把握済み（1004行、3ダイアログクラス含む）
- tkinter の Variable は TranslatorApp 側に残す（controller が読む）

## Sprint 1: SettingsWindow 分離

**Goal**: 設定ウィジェットを別ウィンドウに移動。メインウィンドウから「設定」ボタンで開閉。

**Demo/Validation**:
- アプリ起動 → メインウィンドウに設定フレームがない
- 「設定」ボタン → 設定ウィンドウ開く → 全設定項目が表示される
- 設定変更 → 翻訳開始 → 正常動作
- 設定ウィンドウ閉じる → 自動保存
- `python -m pytest tests/ -q` 全パス

### Task 1.1: tk.Variable をメソッドに分離

- **Location**: `realtime_translator/app.py` — `_build_ui()` メソッド内
- **Description**: `_build_ui()` の冒頭で全 `tk.*Var` を作成している部分を `_create_variables()` メソッドに切り出す。ウィジェット作成とは独立に変数を初期化できるようにする。
- **Dependencies**: なし
- **Details**:
  - 抽出する変数（23個）：
    - `_stt_backend_var`, `_llm_backend_var`
    - `_api_key_var`, `_openai_api_key_var`, `_openrouter_api_key_var`
    - `_gemini_model_var`, `_openai_chat_model_var`, `_openai_stt_model_var`, `_openrouter_model_var`
    - `_loopback_var`, `_mic_var`
    - `_interval_var`, `_vad_var`
    - `_enable_listen_var`, `_enable_speak_var`, `_ptt_var`, `_two_phase_var`, `_show_original_var`
    - `_whisper_var`, `_whisper_model_var`, `_whisper_lang_var`
    - `_threshold_listen_var`, `_threshold_speak_var`
  - `_context_text`（`tk.Text` ウィジェット）は Variable ではないので、代わりに `self._context: str` 属性を追加。設定ウィンドウの Text ウィジェットはこの属性を読み書きする。
  - `_status_var` はメインウィンドウ専用なので `_build_ui()` に残す。
- **Acceptance Criteria**:
  - `_create_variables()` が全23変数を初期化
  - `_build_ui()` が `_create_variables()` を最初に呼ぶ
  - 動作変更なし
- **Validation**: 既存テスト全パス

### Task 1.2a: settings_window.py ウィンドウ骨格作成

- **Location**: `realtime_translator/settings_window.py`（新規）
- **Description**: `SettingsWindow` クラスの骨格と Toplevel ウィンドウの基本構造を作成。
- **Dependencies**: Task 1.1
- **Details**:
  - `SettingsWindow.__init__(self, parent, app)` — `app` は TranslatorApp インスタンス。変数・コールバックに直接アクセス。
  - Toplevel ウィンドウ作成、タイトル・サイズ設定
  - `is_open() -> bool` メソッド、`focus()` メソッド
  - `WM_DELETE_WINDOW` ハンドラ（context 書き戻し + 保存 + destroy）
  - 下部に `[設定保存]` `[閉じる]` ボタン
  - 全セクションのフレーム配置（中身は空でOK）
- **Acceptance Criteria**:
  - 空の設定ウィンドウが開閉できる
  - `is_open()` / `focus()` が動作する
- **Validation**: import して構文エラーなし

### Task 1.2b: settings_window.py 変数バインド・ウィジェット移植

- **Location**: `realtime_translator/settings_window.py`
- **Description**: app.py から設定ウィジェットを移植し、TranslatorApp の Variable にバインド。
- **Dependencies**: Task 1.2a
- **Details**:
  - 移動するセクション（app.py _build_ui からの行番号）：
    - バックエンド設定（L94-217）：`backend_frame` + `_update_backend_visibility`
    - API設定（L115-180）：`api_frame` + 全キー/モデルウィジェット
    - デバイス設定（L219-231）：`dev_frame` + combo + 更新ボタン
    - 翻訳コンテキスト（L233-238）：`ctx_frame` + `_context_text`
    - チャンク間隔 + VAD（L240-252）：`interval_frame` + radios + VAD cb
    - 無音閾値（L273-299）：`threshold_frame` + scales
    - Whisper設定（L301-320）：`whisper_frame`
  - デバイスコンボは `loopback_combo`, `mic_combo` プロパティで公開
  - `context_text` プロパティで tk.Text ウィジェット公開
  - `_update_backend_visibility` のロジックも設定ウィンドウ内に移動
  - 描画専用の更新口を公開（Task 1.3 の状態同期から呼ばれる）：
    - `apply_recording_option_state(ptt_on, vad_on, interval_enabled, two_phase_enabled)` — interval radios / VAD cb / two_phase の state を更新
  - open 時に cached device list と saved selection を注入（`_deferred_init` より後に開かれた場合に対応）
- **Acceptance Criteria**:
  - 設定ウィンドウに全設定項目が表示される
  - バックエンド切替で表示/非表示が連動する
  - コンボボックスが TranslatorApp の Variable にバインドされている
- **Validation**: 手動確認 + 既存テスト全パス

### Task 1.3: app.py からの設定フレーム削除 + 設定状態同期の再設計

- **Location**: `realtime_translator/app.py`, `realtime_translator/settings_window.py`
- **Description**: `_build_ui()` から設定関連フレームを削除し、「設定」ボタンと `_open_settings()` を追加する。あわせて、`_ptt_var` / `_vad_var` / `_two_phase_var` の相互依存を「ウィジェット直接操作」から切り離し、UI 非依存の状態同期メソッド経由に統一する。
- **Dependencies**: Task 1.2b
- **Details**:
  - `_build_ui()` から削除するセクション：
    - `backend_frame`（L94-217）
    - `api_frame`（L115-180）
    - `dev_frame`（L219-231）
    - `ctx_frame`（L233-238）
    - `interval_frame`（L240-252）
    - `threshold_frame`（L273-299）
    - `whisper_frame`（L301-320）
  - ストリーム制御（L254-271）はメインウィンドウに残す
  - ボタン行に「設定」ボタン追加、「設定保存」ボタン削除
  - `self._settings_win: SettingsWindow | None = None` 属性追加
  - `_open_settings()` メソッド追加：
    ```python
    def _open_settings(self):
        if self._settings_win and self._settings_win.is_open():
            self._settings_win.focus()
            return
        self._settings_win = SettingsWindow(self.root, self)
        self._sync_recording_option_state()
    ```
  - `trace_add()` の責務を変更し、`_ptt_var` / `_vad_var` の trace は `_interval_radios` や `_vad_cb` を直接触らず、`_sync_recording_option_state()` のみを呼ぶ
  - `app.py` に UI 非依存の同期メソッドを追加：
    - `_sync_recording_option_state()`:
      - `ptt_enabled` / `vad_enabled` / `two_phase_enabled` の整合性を一箇所で決める
      - PTT ON 時は VAD を無効扱いにする
      - 外部STT使用時は `two_phase` を無効にする
      - 設定ウィンドウが開いている場合のみ `SettingsWindow.apply_recording_option_state()` を呼んで描画更新
  - `_load_config()`、`_open_settings()`、バックエンド変更時にも `_sync_recording_option_state()` を呼び、ウィンドウ開閉状態に関係なく内部状態を先に正にする
- **Acceptance Criteria**:
  - メインウィンドウに設定フレームがない
  - 「設定」ボタンで設定ウィンドウが開く
  - 二重起動防止（既存ウィンドウにフォーカス）
  - 設定ウィンドウを閉じた状態で PTT/VAD/two_phase の値が変わっても、再オープン時に UI が内部状態と一致する
  - `trace_add()` が移動済みウィジェットを直接参照しない
- **Validation**: 手動確認 + 既存テスト全パス

### Task 1.4: デバイス・設定永続化のリファクタ

- **Location**: `realtime_translator/app.py` — `_refresh_devices`, `_get_device_index`, `_save_config`, `_load_config`
- **Description**: 設定ウィンドウのコンボを参照するよう更新。ウィンドウが閉じている場合のフォールバック。
- **Dependencies**: Task 1.3
- **Details**:
  - `_refresh_devices()`: 設定ウィンドウが開いていればそのコンボを更新、閉じていればデバイスリストのみ保存
    ```python
    def _refresh_devices(self):
        # デバイスリスト更新（常に実行）
        self._loopback_devices = enum_devices(loopback=True, pa=self._pa)
        self._mic_devices = enum_devices(loopback=False, pa=self._pa)
        # 設定ウィンドウのコンボ更新（開いている場合のみ）
        if self._settings_win and self._settings_win.is_open():
            self._set_combo(self._settings_win.loopback_combo, ...)
            self._set_combo(self._settings_win.mic_combo, ...)
            self._restore_device_selection()
    ```
  - `_get_device_index()`: Variable の値とデバイスリストから検索（コンボ不要）
    ```python
    def _get_device_index(self, var: tk.StringVar, devices: list[dict]) -> int | None:
        name = var.get()
        for d in devices:
            if d["name"] == name:
                return d["index"]
        return None
    ```
  - `_start_inner()`: `_get_device_index(self._loopback_var, ...)` に変更
  - `_save_config()`: `self._context_text` → 設定ウィンドウが開いていれば `self._settings_win.context_text` から、閉じていれば `self._context` 属性から読む
  - `_load_config()`: context を `self._context` に保存。設定ウィンドウが開いていれば Text ウィジェットも更新
  - `_restore_device_selection()`: Variable の `.set()` を使う（コンボの `.current()` ではなく）
- **Acceptance Criteria**:
  - 設定ウィンドウを開かずに翻訳開始できる
  - 設定ウィンドウ開閉してもデバイス選択が保持される
  - 設定保存/読込が正常動作
- **Validation**: 既存テスト全パス + 手動確認

## Sprint 2: ツールパネル統合

**Goal**: 再翻訳・返答アシスト・議事録を横並びパネルとしてメインウィンドウに埋め込む。

**Demo/Validation**:
- メインウィンドウ下部に3パネルが横並び表示
- 再翻訳パネル：翻訳が来ると最新エントリを自動選択
- 各パネルの実行ボタンが動作する
- 結果表示エリアとツールパネルのリサイズが可能
- `python -m pytest tests/ -q` 全パス

### Task 2.1: tools_panel.py 新規作成 — 骨格

- **Location**: `realtime_translator/tools_panel.py`（新規）
- **Description**: 3パネル横並びの `ToolsPanel` クラスを作成。
- **Dependencies**: Sprint 1 完了
- **Details**:
  - `ToolsPanel.__init__(self, parent, controller)`:
    - `self.frame = ttk.Frame(parent)` — 外側コンテナ
    - 横3列 `columnconfigure(0/1/2, weight=1, uniform="tool")`
    - 各パネルは `ttk.LabelFrame`
  - 内部状態：
    - `_pending_retrans_id: str = ""`
    - `_pending_assist_id: str = ""`
    - `_pending_minutes_id: str = ""`
  - パブリックメソッド（シグネチャのみ、実装は後続タスク）：
    - `update_latest_entry(entry: HistoryEntry)` — 再翻訳パネル更新
    - `refresh_history()` — 再翻訳リスト再構築
    - `on_retrans_result(batch_id, seq, text)` / `on_retrans_error(batch_id, msg)`
    - `on_assist_result(request_id, text)` / `on_assist_error(request_id, msg)`
    - `on_minutes_result(request_id, text)` / `on_minutes_error(request_id, msg)`
    - `set_button_states(retranslate_enabled, assist_enabled, minutes_enabled)` — 個別ボタン状態制御（`can_retranslate()` / `can_assist()` に基づく）
    - `reset()` — セッション境界でのクリア（pending ID リセット、リスト・結果欄・最新ラベル初期化、ステータス表示リセット）
- **Acceptance Criteria**:
  - 空の3カラムフレームが表示される
  - パブリックメソッドが呼べる（stub）
- **Validation**: import して構文エラーなし

### Task 2.2: 再翻訳パネル実装

- **Location**: `realtime_translator/tools_panel.py` — `_build_retrans_panel()`
- **Description**: 再翻訳パネルの UI とロジック。最新エントリの自動更新機能。
- **Dependencies**: Task 2.1
- **Details**:
  - レイアウト（LabelFrame "再翻訳" 内）：
    ```
    [最新: #5 [12:30:45] PC音声: Hello world...     ]  ← Label（自動更新）
    [Listbox (height=6, 全履歴)                      ]  ← 選択可能
    [前後の範囲: [Spinbox] 件  [再翻訳 実行] Status  ]
    [結果テキスト (ScrolledText, height=4)            ]
    ```
  - `update_latest_entry(entry)`:
    - 最新ラベル更新: `"最新: #{seq} [{ts}] {label}: {original[:40]}..."`
    - リストに追加（`_refresh_list` を再構築ではなく追記）
    - 最新エントリを自動選択 + スクロール
  - `_refresh_list()`: controller.history.all_entries() から再構築
  - `_execute_retrans()`:
    - 選択がない場合は最新エントリを使用
    - `controller.request_retranslation(seq, range)` を呼ぶ
    - `_pending_retrans_id` に batch_id を保存
  - `on_retrans_result(batch_id, seq, text)`:
    - `_pending_retrans_id` と一致する場合のみ処理
    - 結果テキストに表示: `"#{seq} 元の訳文: {translation}\n    再翻訳: {text}"`
  - `on_retrans_error(batch_id, msg)`: エラー表示
- **Acceptance Criteria**:
  - 翻訳が来るたびにリスト＋最新ラベルが更新
  - 最新エントリが自動選択される
  - 履歴から任意の項目を選んで再翻訳できる
  - 選択なし時は最新を使う
- **Validation**: 手動確認

### Task 2.3: 返答アシスト・議事録パネル実装

- **Location**: `realtime_translator/tools_panel.py` — `_build_assist_panel()`, `_build_minutes_panel()`
- **Description**: 残り2パネルの UI とロジック。
- **Dependencies**: Task 2.1
- **Details**:
  - **返答アシストパネル** (LabelFrame "返答アシスト"):
    ```
    [[アシスト実行] Status                          ]
    [結果テキスト (ScrolledText, height=8, disabled) ]
    ```
    - `_execute_assist()`: `controller.request_reply_assist()` → `_pending_assist_id`
    - `on_assist_result(request_id, text)`: テキスト表示
  - **議事録パネル** (LabelFrame "議事録"):
    ```
    [[生成] [エクスポート] Status                    ]
    [テキスト (ScrolledText, height=8, editable)     ]
    ```
    - `_execute_minutes()`: `controller.request_minutes(previous_minutes=current_text)` → `_pending_minutes_id`
    - `on_minutes_result(request_id, text)`: テキスト挿入
    - `_export_minutes()`: filedialog でテキスト保存
  - `set_enabled(enabled)`: 全実行ボタンの state を "normal"/"disabled" に
- **Acceptance Criteria**:
  - アシスト実行 → 結果表示
  - 議事録生成 → テキスト表示 → エクスポート可
  - 翻訳停止時はボタン無効化
- **Validation**: 手動確認

### Task 2.4: app.py にツールパネル統合 + PanedWindow

- **Location**: `realtime_translator/app.py` — `_build_ui()`, `_poll_queue()`, `_start`, `_stop`, `_clear_result()`
- **Description**: メインウィンドウに PanedWindow + ToolsPanel を組み込み、ダイアログ関連コードを削除する。`translation_done` では `history.append()` が返す `HistoryEntry` を ToolsPanel へ渡す。ツールボタンの有効/無効を `_sync_tool_states()` で一元管理する。
- **Dependencies**: Task 2.2, Task 2.3
- **Details**:
  - `_build_ui()` 変更：
    ```python
    # PanedWindow (vertical, 7:3)
    paned = ttk.PanedWindow(self.root, orient="vertical")
    paned.pack(fill="both", expand=True, padx=8, pady=4)

    # 上: 翻訳結果
    result_frame = ttk.LabelFrame(paned, text="翻訳結果")
    self._result_text = scrolledtext.ScrolledText(...)
    paned.add(result_frame, weight=7)

    # 下: ツールパネル（横3列）
    self._tools_panel = ToolsPanel(paned, self._controller)
    paned.add(self._tools_panel.frame, weight=3)
    ```
  - ボタン行から `再翻訳...`, `返答アシスト`, `議事録` ボタン削除
  - `app.py` に `_sync_tool_states()` を追加：
    ```python
    def _sync_tool_states(self) -> None:
        if self._tools_panel is None:
            return
        self._tools_panel.set_button_states(
            retranslate_enabled=self._controller.can_retranslate(),
            assist_enabled=self._controller.can_assist(),
            minutes_enabled=self._controller.can_assist(),
        )
    ```
  - `_poll_queue()` 変更：
    - `retrans_result` → `self._tools_panel.on_retrans_result(batch_id, seq, text)`
    - `retrans_error` → `self._tools_panel.on_retrans_error(batch_id, msg)`
    - `assist_result` → request_type に応じて `on_assist_result` or `on_minutes_result`
    - `assist_error` → request_type に応じて `on_assist_error` or `on_minutes_error`
    - `translation_done`:
      ```python
      entry = self._controller.history.append(stream_id, ts, original, translation)
      self._tools_panel.update_latest_entry(entry)
      self._sync_tool_states()
      ```
    - `_result_store` 辞書を廃止（ToolsPanel が直接処理）
  - `_start` 変更：
    - 起動成功時に `self._tools_panel.reset()` を呼びセッション初期化
    - 直後に `self._sync_tool_states()` を呼ぶ
  - `_stop` 変更：
    - 停止完了後に `self._tools_panel.reset()` + `self._sync_tool_states()` を呼ぶ
  - `_clear_result()` 変更：
    - 翻訳結果クリア後に `self._tools_panel.refresh_history()` + `self._sync_tool_states()` を呼ぶ
  - 削除するクラス・メソッド：
    - `RetranslationDialog` クラス全体
    - `ReplyAssistDialog` クラス全体
    - `MinutesDialog` クラス全体
    - `_open_retranslation`, `_open_reply_assist`, `_open_minutes` メソッド
    - `_retrans_dialog`, `_assist_dialog`, `_minutes_dialog` 属性
    - `_result_store` 辞書
- **Acceptance Criteria**:
  - メインウィンドウに翻訳結果（上）とツールパネル（下）が表示
  - ドラッグでリサイズ可能
  - ダイアログウィンドウが開かない
  - ツールパネルの全機能が動作
  - `translation_done` 後にボタン状態が即時更新（`can_assist()` 反映）
  - `_start` / `_stop` / `_clear_result()` でツールボタン状態が `can_retranslate()` / `can_assist()` と一致
  - 停止→再開でセッション間の結果が混線しない
- **Validation**: 手動確認 + 既存テスト全パス

### Task 2.5: メインウィンドウのレイアウト調整

- **Location**: `realtime_translator/app.py`
- **Description**: ウィンドウサイズ、ボタン配置、ステータスバーの最終調整。
- **Dependencies**: Task 2.4
- **Details**:
  - デフォルトウィンドウサイズ: `self.root.geometry("1100x750")`
  - ボタン行のレイアウト：
    ```
    [▶ 開始] [設定] [クリア] [エクスポート] [PTTボタン]  状態: ...
    ```
  - ストリーム制御は翻訳結果の上（コンパクトに1行）
  - PTTフレームの pack 位置調整（`after=` 対象変更）
- **Acceptance Criteria**:
  - 起動時に適切なサイズで表示
  - ボタンが整列
  - PTTボタンが正しい位置に表示/非表示
- **Validation**: 手動確認

## Sprint 3: テスト・仕上げ

**Goal**: テスト更新、エッジケース対処、最終確認。

**Demo/Validation**:
- `python -m pytest tests/ -q` 全パス
- 設定ウィンドウ未オープンで翻訳開始
- 翻訳中に設定変更 → 次回開始に反映
- 設定ウィンドウ閉じ → 自動保存

### Task 3.1: テスト更新（必須）

- **Location**: `tests/test_integration.py`, `tests/test_tools_panel.py`（新規）
- **Description**: 設定ウィンドウ分離・ダイアログ廃止に伴うテスト更新。ToolsPanel の結線テストを追加。
- **Dependencies**: Sprint 2 完了
- **Details**:
  - `TestShowOriginal` テスト: `app._show_original_var` と `app._result_text` はメインに残るので変更不要
  - ダイアログ系テストがあれば削除/移行
  - **必須テスト追加**:
    - `translation_done` → `update_latest_entry` で最新選択が更新されること
    - 設定ウィンドウ未オープンで `_start_inner()` が context/device を正しく読めること
    - 停止→再開で pending 結果が混線しないこと（`reset()` によるクリア確認）
    - `_sync_tool_states()` が `can_retranslate()` / `can_assist()` と一致すること
    - `show_original=False` 時の回帰テストが ToolsPanel 統合後も維持されること
- **Acceptance Criteria**:
  - 全既存テストパス
  - 新規テスト 5件以上パス
- **Validation**: `python -m pytest tests/ -q`

### Task 3.2: エッジケース対処

- **Location**: `realtime_translator/app.py`, `realtime_translator/settings_window.py`
- **Description**: 設定ウィンドウ未オープン時の動作、翻訳中の設定変更など。
- **Dependencies**: Task 3.1
- **Details**:
  - 設定ウィンドウ未オープンで翻訳開始 → Variable は load_config で既に設定済みなので動作する
  - 翻訳中にデバイス変更 → 次回開始時に反映（現行と同じ）
  - コンテキストの同期:
    - 設定ウィンドウ開く時: `self._context` → Text ウィジェットに反映
    - 設定ウィンドウ閉じる時: Text ウィジェット → `self._context` に書き戻し
    - `_start_inner()` で context 取得: 設定ウィンドウ開いていれば Text から、閉じていれば `self._context` から
  - `on_close()` で設定ウィンドウが開いていれば先に閉じる
  - `_clear_result()` で `tools_panel.refresh_history()` を呼ぶ（履歴クリア連動）
- **Acceptance Criteria**:
  - 全シナリオで例外なし
  - 状態の不整合なし
- **Validation**: 手動テスト全パターン

## Testing Strategy

- **既存テスト**: `python -m pytest tests/ -q` — Sprint 毎に実行
- **手動テスト**:
  1. 起動 → 設定未変更で翻訳開始（デフォルト値で動作確認）
  2. 設定ウィンドウ開く → 設定変更 → 翻訳開始
  3. 翻訳中に設定ウィンドウ開閉
  4. 再翻訳パネル: 自動更新 → 手動選択 → 再翻訳実行
  5. アシスト/議事録パネル: 実行 → 結果表示
  6. PanedWindow リサイズ
  7. 設定保存 → アプリ再起動 → 復元確認

## Potential Risks & Gotchas

1. **`_context_text` のライフサイクル**: Text ウィジェットは設定ウィンドウにしか存在しない。`_save_config` / `_start_inner` で参照する場所を確実にフォールバック対応する。→ `self._context: str` を single source of truth とし、設定ウィンドウ open/close 時に同期。
2. **`_refresh_devices` のタイミング**: 設定ウィンドウが閉じている時の `_deferred_init` → コンボを更新できない。→ デバイスリストを `self._loopback_devices` に保存し、設定ウィンドウ open 時にコンボを populated。
3. **PTT/VAD/two_phase 状態同期**: interval radio / VAD cb / two_phase が設定ウィンドウ内に移動。→ `_sync_recording_option_state()` で UI 非依存に整合性を計算し、設定ウィンドウが開いていれば描画更新。閉じた状態で値が変わっても再オープン時に一致する。
4. **`_get_device_index` の変更**: 現在は combo.current() を使うが、Variable ベースに変更。→ デバイス名で devices リストを検索する方式に。
5. **横並び3パネルの幅**: ウィンドウ幅が狭いとテキストが見づらい。→ `uniform="tool"` で均等分割 + min width 考慮。
6. **`stop()` 時の UI キュー破棄**: `TranslatorController.stop()` は join 後にキューを drain して捨てるため、停止直前に返った `retrans_result` / `assist_result` を `_poll_queue()` が受け取れない可能性がある。常設パネルでは「実行中表示のまま固まる」不整合になる。→ `_stop()` で `tools_panel.reset()` を呼び pending 状態を強制クリアする。
7. **セッション切替後の旧結果混入**: `start()` は毎回 `history.clear()` して seq を 1 から振り直すが、非同期ワーカーの結果は `batch_id` / `request_id` 経由で後から届く。→ `_start()` で `tools_panel.reset()` し pending ID をクリアすることで、旧セッションの結果を無視する。
8. **`show_original=False` 時の ToolsPanel 表示**: メイン結果欄では原文を非表示にするが、再翻訳パネルの「最新」ラベルには `entry.original` を表示する。→ 再翻訳パネルは翻訳対象選択のためのUIなので、`show_original` に関係なく原文を表示する仕様とする。
9. **アシスト/再翻訳の実行時点の履歴差**: `AssistWorker` は submit 時ではなく処理直前に `history.all_entries()` を再取得する。ボタン押下後に履歴が増え結果内容がズレる可能性がある。→ 既存動作であり今回のスコープ外。将来的に submit 時に `max_seq` を固定する設計も可能。

## Rollback Plan

- 全変更は `app.py` の修正 + 2新規ファイル
- `git stash` or `git checkout -- realtime_translator/app.py` + 新規ファイル削除で即座に元に戻せる
- Sprint 1 完了後にコミットし、Sprint 2 で問題があれば Sprint 1 まで戻れる

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **[High] `translation_done` の受け口が未定義**: Task 2.4 に `entry = history.append(...)` を受けて ToolsPanel に渡す実装ステップを明示
- **[High] ツールボタン有効/無効制御不完全**: `_sync_tool_states()` メソッドを Task 2.4 に追加。`_start` / `_stop` / `_clear_result()` / `translation_done` の全箇所で再評価するよう明記
- **[High] PTT/VAD trace が移動済みウィジェットを直接参照**: Task 1.3 を「設定状態同期の再設計」に書き直し。`_sync_recording_option_state()` で UI 非依存に整合性を計算し、SettingsWindow には描画専用の `apply_recording_option_state()` のみ公開
- **[High] セッションまたぎの残留結果対策**: Task 2.1 に `reset()` メソッドを追加。Task 2.4 で `_start` / `_stop` 時に呼ぶよう明記
- **[High] 新規開始時の履歴リセット整合**: Task 2.4 の `_start` で `tools_panel.reset()` を呼ぶよう追加
- **[Medium] デバイス初期化順序**: Task 1.2b に「open 時に cached device list と saved selection を注入」を追加
- **[High] テスト計画が弱い**: Task 3.1 を「オプション」→「必須」に変更。具体的な必須テスト5件を列挙
- **[Medium] Task 1.2 が大きすぎ**: Task 1.2 を 1.2a（骨格）+ 1.2b（変数バインド・移植）に分割
- **[Hidden] stop() 時の UI キュー破棄**: Gotcha #6 として追加
- **[Hidden] セッション切替後の旧結果混入**: Gotcha #7 として追加
- **[Hidden] show_original=False 時の ToolsPanel 表示方針**: Gotcha #8 として追加（再翻訳パネルは常に原文表示する仕様）

### Skipped Feedback
- **アシスト/再翻訳の実行時点の履歴差**: 既存動作であり今回のリファクタリングスコープ外。Gotcha #9 に参考情報として記載のみ
- **再翻訳の center_seq 存在チェック**: `reset()` による pending クリアで対処済み。別途 session token 方式は過剰設計
- **テスト面の回帰テスト移行（Step 3 指摘）**: Task 3.1 の必須テストに統合済みのため個別タスク化は不要
