# Plan: レスポンス高速化

**Generated**: 2026-03-08
**Estimated Complexity**: High

## Overview

翻訳パイプライン全体のレイテンシを削減する。主要ボトルネック（レート制限4s、チャンク蓄積1-3s、2フェーズ8s）を解消し、バックエンド構成に応じて最適な速度を実現する。

### 現状のレイテンシ構造
```
音声蓄積(1-3s) → APIレート制限(4s) → ストリーミング応答(0.2-0.5s) → UIポーリング(10-100ms)
```

### 目標
- Gemini Free tier: 5s → 4s（レート制限は不可避）
- OpenAI/OpenRouter: 5s → 1.5-2s
- PTT + OpenAI: 1.5s → 0.8-1s
- 2フェーズ: 10s → 使用非推奨化 + 警告表示

## Prerequisites

- 現行テストが全パス（`python -m pytest tests/ -q`）
- settings-window-refactor 完了済み（SettingsWindow, ToolsPanel 分離済み）

## Sprint 1: バックエンド別レート制限の動的設定

**Goal**: `MIN_API_INTERVAL_SEC` を一律4.0sではなく、バックエンド＋プランに応じて動的に設定。

**Demo/Validation**:
- OpenAI バックエンドで翻訳 → 1秒未満の間隔で応答
- Gemini バックエンドで翻訳 → 4秒間隔を維持
- 設定ウィンドウで API 間隔を調整可能

### Task 1.1: constants.py にバックエンド別デフォルト間隔を追加

- **Location**: `realtime_translator/constants.py`
- **Description**: `MIN_API_INTERVAL_SEC` を残しつつ、バックエンド別のデフォルト間隔辞書を追加。
- **Dependencies**: なし
- **Details**:
  - 追加する定数:
    ```python
    MIN_API_INTERVAL_BY_BACKEND: dict[str, float] = {
        "gemini": 4.0,      # Free tier 15RPM
        "openai": 0.5,      # Rate limit generous
        "openrouter": 0.5,  # Rate limit generous
        "whisper": 0.0,     # Local, no rate limit
    }
    ```
  - `MIN_API_INTERVAL_SEC` は後方互換のため残す（デフォルトフォールバック）
- **Acceptance Criteria**:
  - 辞書が正しく定義されている
  - 既存コードは影響を受けない
- **Validation**: 既存テスト全パス

### Task 1.2: controller.py の LLM ワーカー間隔をバックエンド別に解決する

- **Location**: `realtime_translator/controller.py`
- **Description**: `start()` / `_start_workers()` で生成する `ApiWorker` / `OpenAiLlmWorker` の `min_interval_sec` を、LLM バックエンド単位で解決して渡す。対象はあくまで翻訳 LLM ワーカーであり、`OpenAiSttWorker` 自体の実行間隔はこのタスクでは扱わない。
- **Dependencies**: Task 1.1
- **Details**:
  - `_resolve_api_interval(backend: str) -> float` ヘルパーを追加:
    ```python
    def _resolve_api_interval(self, backend: str) -> float:
        from .constants import MIN_API_INTERVAL_BY_BACKEND, MIN_API_INTERVAL_SEC
        return MIN_API_INTERVAL_BY_BACKEND.get(backend, MIN_API_INTERVAL_SEC)
    ```
  - `ApiWorker` / `OpenAiLlmWorker` 生成時の `min_interval_sec` は `self._resolve_api_interval(config.llm_backend)` を基本値とする
  - 現在の `use_whisper` / `use_openai_stt` 時の `1.0s` 緩和は「外部 STT 使用時に phase=2 翻訳だけを速める LLM 側の暫定上書き」として明示し、必要なら `_resolve_llm_interval(llm_backend, stt_backend)` のような専用ヘルパーに切り出す
  - `OpenAiSttWorker` には `min_interval_sec` を渡さない。STT 側の制御は Task 1.3 で queue/backpressure として扱う
- **Acceptance Criteria**:
  - Gemini LLM は既定で `4.0s` を使う
  - OpenAI / OpenRouter LLM は既定で `0.5s` を使う
  - `stt_backend="openai"` 時も、変更対象が LLM ワーカーだけであることがコード上で明確
- **Validation**: `tests/test_controller.py` に LLM ワーカーの `min_interval_sec` 解決テストを追加し、既存テスト全パス

### Task 1.3: OpenAI STT の queue/backpressure 方針を整理する

- **Location**: `realtime_translator/openai_stt.py`, `tests/test_openai_stt.py`
- **Description**: `OpenAiSttWorker` は `min_interval_sec` ではなく `_req_queue(maxsize=3)` と oldest-drop で負荷制御されている。この実装事実に合わせて、STT 側は「queue 長・drop 方針」を明文化し、必要なテストを追加する。
- **Dependencies**: Task 1.2
- **Details**:
  - `OpenAiSttWorker.submit()` の backpressure を仕様化:
    - queue が満杯なら oldest を捨てて最新チャンクを優先
    - `pending_requests` が queue 操作と整合することを保証
  - 必要なら定数化: `OPENAI_STT_QUEUE_MAXSIZE = 3`
  - `min_interval_sec` 導入はこの Sprint の対象外
- **Acceptance Criteria**:
  - STT 側の制御方式が queue/backpressure であることが計画文面とコードで一致
  - queue full 時の oldest-drop 動作がテストで固定化される
- **Validation**: `python -m pytest tests/test_openai_stt.py -v`

### Task 1.4: StartConfig にカスタム API 間隔フィールドを追加

- **Location**: `realtime_translator/controller.py` — `StartConfig`
- **Description**: ユーザーがカスタム API 間隔を設定できるオプションフィールドを追加。
- **Dependencies**: Task 1.2
- **Details**:
  - `StartConfig` に追加:
    ```python
    custom_api_interval: float | None = None  # None = バックエンド自動判定
    ```
  - `_resolve_api_interval` でカスタム値があればそちらを優先:
    ```python
    def _resolve_api_interval(self, backend: str, custom: float | None = None) -> float:
        if custom is not None:
            return max(0.0, custom)
        return MIN_API_INTERVAL_BY_BACKEND.get(backend, MIN_API_INTERVAL_SEC)
    ```
- **Acceptance Criteria**:
  - `custom_api_interval=1.0` → 全ワーカーが 1.0s 間隔
  - `custom_api_interval=None` → バックエンド自動判定
- **Validation**: 既存テスト全パス（デフォルト値 None で後方互換）

### Task 1.5: 設定UIに API 間隔スライダーを追加（独立キーで永続化）

- **Location**: `realtime_translator/settings_window.py`, `realtime_translator/app.py`, `realtime_translator/config.py`
- **Description**: 既存の `interval`（録音チャンク秒数, 3/5/8 固定）とは独立した設定キー `api_interval` を新設し、設定UIから API 呼び出し間隔を編集できるようにする。UI ラベルは「翻訳API呼び出し間隔」とし、対象が翻訳 LLM ワーカーに限定されることを明示する。
- **Dependencies**: Task 1.4
- **Details**:
  - `app.py` の `_create_variables()` に API 間隔専用の変数を追加し、既存の `self._interval_var` とは完全に分離:
    ```python
    self._api_interval_var = tk.DoubleVar(value=0.0)  # 0.0 = 自動
    ```
  - `settings_window.py` に API 間隔専用 UI を追加。既存のチャンク間隔 UI とは別 row / 別 label / 別バインディングにし、`interval` を上書きしない:
    - ラベル `"翻訳API呼び出し間隔:"`
    - Scale: `from_=0.0, to=5.0, resolution=0.5`
    - 値ラベル: `0.0 -> "自動"`, `>0.0 -> "X.Xs"`
  - `config.py` に `api_interval` 専用 sanitize を追加:
    - `interval` は従来どおり `3/5/8` の整数制約を維持
    - `api_interval` は独立した float キーとして扱い、`0.0` 以上のみ許可
    - 不正値は `0.0` にフォールバック
  - `save_config()` / `load_config()` に `api_interval` を追加し、`interval` の sanitize と混線させない
  - 旧設定に `api_interval` がなければ `0.0` を補完。`interval` 値は移行対象にしない
  - `app.py` の `_start_inner()` では `self._api_interval_var.get()` を `custom_api_interval` に渡す
- **Acceptance Criteria**:
  - `interval` と `api_interval` が別キーで保存される
  - `api_interval=1.5` を保存しても `interval` の sanitize で壊れない
  - `api_interval` 未設定時は `0.0`（自動）で起動する
  - 既存の `interval` 設定と録音チャンク挙動に回帰がない
- **Validation**: 手動確認 + `tests/test_config.py` の永続化テスト追加後に全件パス

## Sprint 2: 短チャンクオプション + VAD 改善

**Goal**: 音声蓄積時間を短縮し、発話終了をより速く検出する。

**Demo/Validation**:
- 1秒チャンクで翻訳 → 高速だがフラグメント化の可能性
- VAD モードで発話終了 → 即時送信（蓄積 ~0.8s）

### Task 2.1: VAD と continuous のチャンク設定責務を確定し、短チャンクを追加

- **Location**: `realtime_translator/app.py`, `realtime_translator/settings_window.py`, `realtime_translator/record_strategies.py`, `realtime_translator/config.py`
- **Description**: 1秒・2秒の短チャンク追加に先立ち、`chunk_seconds` の責務を明確化する。現行実装では VAD 有効時に `interval` UI は無効化される一方、`record_strategies.py` の `VADStrategy` は `chunk_seconds` を最大発話長計算に使用しており、UI と実装の責務が不一致。方針を確定した上で短チャンクオプションを追加する。
- **Dependencies**: なし
- **Details**:
  - 方針決定: `chunk_seconds` を continuous 専用にし、VAD 用最大発話長は `chunk_seconds * 2` の計算式で暗黙決定される現行方式を維持する（VAD 有効時は UI で編集不可のまま）
  - `settings_window.py` の `_build_chunk_vad_section` を更新:
    ```python
    for i, (text, val) in enumerate([("1秒", 1), ("2秒", 2), ("3秒", 3), ("5秒", 5), ("8秒", 8)]):
    ```
  - `_interval_var` のデフォルト値は 5 のまま（後方互換）
  - `config.py` の interval バリデーション: min=1 に変更
  - VAD 有効時に interval UI が無効化される動作は維持。VAD の最大発話長は Task 2.3 で別途保証
- **Acceptance Criteria**:
  - 1秒・2秒が選択可能（continuous モード時）
  - VAD 有効時は interval UI が無効化されたまま
  - 翻訳が正常に動作（短チャンクでも）
  - 設定保存・復元が動作
- **Validation**: 手動確認 + config の移行テスト

### Task 2.2: VAD の無音検出閾値を短縮

- **Location**: `realtime_translator/record_strategies.py`
- **Description**: VAD の無音検出トリガーを 0.8s → 0.5s に短縮。
- **Dependencies**: なし
- **Details**:
  - `VADStrategy.__init__` の `_silence_trigger` 計算:
    ```python
    # 現在: max(1, int(sample_rate * 0.8 / AUDIO_CHUNK_SIZE))
    # 変更: max(1, int(sample_rate * 0.5 / AUDIO_CHUNK_SIZE))
    ```
  - 0.5s でも実用的に十分な無音検出が可能
  - 定数化: `VAD_SILENCE_SECONDS = 0.5` を constants.py に追加
- **Acceptance Criteria**:
  - VAD が 0.5s の無音で発話終了を検出
  - 文中のポーズで誤切断しない（webrtcvad の is_speech で保護）
- **Validation**: 手動確認

### Task 2.3: チャンク最大長の見直し

- **Location**: `realtime_translator/record_strategies.py`
- **Description**: VAD の最大蓄積チャンク数を見直し、長い発話でもタイムアウトしすぎないようにする。
- **Dependencies**: Task 2.2
- **Details**:
  - 現在: `max_chunks = int(sample_rate * chunk_seconds * 2 / AUDIO_CHUNK_SIZE)`
  - chunk_seconds=1 の場合: max_chunks = int(48000 * 1 * 2 / 1024) = 93 → ~2秒
  - 短チャンクモードでは max_chunks が短すぎて発話が途中で切れる可能性
  - 変更: `max_chunks = max(int(sample_rate * chunk_seconds * 2 / AUDIO_CHUNK_SIZE), int(sample_rate * 4 / AUDIO_CHUNK_SIZE))`
  - 最低4秒分は蓄積できるように保証
- **Acceptance Criteria**:
  - chunk_seconds=1 でも 4秒の発話を蓄積可能
  - chunk_seconds=5 では従来通り 10秒まで蓄積
- **Validation**: 単体テスト

## Sprint 3: 2フェーズ警告 + UIポーリング最適化

**Goal**: 2フェーズのデメリットを可視化し、UIの体感応答性を改善。

### Task 3.1: 2フェーズ選択時の警告表示（専用ラベル）

- **Location**: `realtime_translator/app.py`, `realtime_translator/settings_window.py`
- **Description**: 2フェーズ有効時に専用の警告ラベルを表示する。ステータスバーは `status` イベントで定常的に上書きされるため、ステータスバーではなく 2フェーズチェックボックスの横に常設の注意文ラベルを配置する。
- **Dependencies**: なし
- **Details**:
  - `settings_window.py` の 2フェーズ チェックボックス横に `ttk.Label` を追加
  - `_two_phase_var` の trace_add で表示/非表示を切り替え:
    ```python
    self._two_phase_warning = ttk.Label(row, text="⚠ 応答が遅くなります", foreground="red")
    def _on_two_phase_toggle(*_):
        if self._two_phase_var.get():
            self._two_phase_warning.grid(...)
        else:
            self._two_phase_warning.grid_remove()
    ```
  - ステータスバーには書き込まない
- **Acceptance Criteria**:
  - 2フェーズ ON → チェックボックス横に警告ラベル表示
  - 2フェーズ OFF → 警告ラベル非表示
  - ステータスバーの通常動作に影響しない
- **Validation**: 手動確認

### Task 3.2: UIポーリング戦略の単純化

- **Location**: `realtime_translator/app.py`
- **Description**: ポーリング間隔を「翻訳中は常時 10ms、停止中は 50ms」に単純化する。`event_generate` 方式は tkinter のスレッドセーフ制約上見送り、アクティブ状態ベースの固定間隔で対応する。
- **Dependencies**: なし
- **Details**:
  - `_poll_queue()` の末尾を変更:
    ```python
    if self._controller.is_running:
        interval = 10
    else:
        interval = 10 if had_items else 50
    ```
  - CPU への影響: 翻訳中 10ms (100回/秒) は微小、停止中 50ms (20回/秒) も微小
  - 体感: 翻訳結果の初回表示が最大 50ms 早くなる（現行 100ms → 50ms）
- **Acceptance Criteria**:
  - 翻訳中は常に 10ms ポーリング
  - 停止中は 50ms（CPU 節約）
  - CPU使用率に有意な増加なし
- **Validation**: 手動確認

## Sprint 4: テスト + 仕上げ

### Task 4.1: バックエンド別間隔のユニットテスト + 既存テスト更新

- **Location**: `tests/test_controller.py`
- **Description**: `_resolve_api_interval` のテスト追加、および `min_interval_sec` の期待値変更に伴う既存テストの更新。
- **Dependencies**: Sprint 1 完了
- **Details**:
  - 新規テストケース:
    - `_resolve_api_interval("gemini")` == 4.0
    - `_resolve_api_interval("openai")` == 0.5
    - `_resolve_api_interval("openrouter")` == 0.5
    - `_resolve_api_interval("unknown")` == 4.0 (fallback)
    - `_resolve_api_interval("gemini", custom=1.0)` == 1.0
    - `_resolve_api_interval("gemini", custom=0.0)` == 0.0
    - `_resolve_api_interval("gemini", custom=None)` == 4.0
  - 既存テスト更新:
    - `1.0s` を前提としたテストの期待値を新しいバックエンド別デフォルト値に更新
    - `StartConfig` に `custom_api_interval` フィールドが追加されるため、全 `_make_config()` ヘルパーのデフォルト引数を確認・更新
- **Acceptance Criteria**: 全ケースパス + 既存テストが新しい間隔値で正しくパス
- **Validation**: `python -m pytest tests/test_controller.py -q`

### Task 4.2: VAD 無音検出のユニットテスト

- **Location**: `tests/test_record_strategies.py`
- **Description**: 短縮された無音検出閾値のテスト。
- **Dependencies**: Sprint 2 完了
- **Details**:
  - `VADStrategy` の `_silence_trigger` が `VAD_SILENCE_SECONDS` に基づいて計算されることを確認
  - chunk_seconds=1 の場合に max_chunks >= 4秒分であることを確認
- **Acceptance Criteria**: テストパス
- **Validation**: `python -m pytest tests/test_record_strategies.py -q`

### Task 4.3: 設定永続化のテスト（api_interval 独立キー検証）

- **Location**: `tests/test_config.py`
- **Description**: `api_interval` が既存 `interval` スキーマと独立して保存・復元・sanitize・移行されることを検証する。
- **Dependencies**: Sprint 1 完了
- **Details**:
  - `{"interval": 5, "api_interval": 1.5}` → `interval == 5`, `api_interval == 1.5`
  - `{"interval": 3, "api_interval": "1.5"}` → `api_interval == 1.5`（文字列→float変換）
  - `{"interval": 5, "api_interval": -1}` → `api_interval == 0.0`（不正値フォールバック）
  - `{"interval": 5, "api_interval": "bad"}` → `api_interval == 0.0`
  - `{"interval": 8}` → `api_interval == 0.0`（欠損値補完）
  - `api_interval=1.5` 保存後も `interval` が `3/5/8` 制約のまま
  - `interval` と `api_interval` の相互汚染がないこと
- **Acceptance Criteria**: テストパス、interval/api_interval が完全に独立
- **Validation**: `python -m pytest tests/test_config.py -q`

## Testing Strategy

- **既存テスト**: `python -m pytest tests/ -q` — Sprint 毎に実行
- **手動テスト**:
  1. Gemini バックエンド: 4秒間隔が維持されることを確認
  2. OpenAI バックエンド: 0.5秒間隔で高速応答
  3. カスタム間隔 1.0s: スライダー設定 → 翻訳 → 1秒間隔
  4. 1秒チャンク: 高速だが断片的な翻訳
  5. VAD: 0.5秒無音で即送信
  6. 2フェーズ ON: 警告表示

## Potential Risks & Gotchas

1. **短チャンクの翻訳品質劣化**: 1秒チャンクでは文脈が少なく、翻訳品質が落ちる可能性。→ UIで「短いチャンクは品質が低下する可能性があります」と注記。
2. **API レート制限超過**: カスタム間隔を 0 にするとレート制限に引っかかる。→ エラーは既存のエラーハンドリングで対処。UI で最低値の推奨を表示。
3. **VAD 無音 0.5s の誤切断**: 話者が一瞬ポーズした場合に誤って分割される。→ webrtcvad の is_speech が保護するが、文中ポーズ 0.3-0.7s の回帰テストを Task 4.2 で追加する。
4. **後方互換**: `custom_api_interval=None` がデフォルトなので既存動作は変わらない。
5. **config.py のバリデーション**: interval の最小値を 1 に変更する必要がある。`api_interval` は独立した float キーとして別途 sanitize する。
6. **停止時の未処理要求ドロップ**: `send_stop_sentinel()` は満杯時に oldest-drop して sentinel を入れるため、停止直前の実リクエストを捨てる。`controller.stop()` も UI queue を全 drain するため、停止直前の `error`/`translation_done`/`status` を UI が見逃す可能性がある。
7. **LLM 側 queue の silent drop**: `API_QUEUE_MAXSIZE=3` が小さいため、短チャンク化や API 間隔短縮で silent drop が増える可能性がある。drop は debug log のみで UI に出ないため、ユーザーが気づけない。必要に応じてキューサイズ調整や drop 通知を検討。
8. **`api_interval` の適用スコープ**: `api_interval` は翻訳 LLM ワーカーのみに適用される。再翻訳・assist は独自の `min_interval_sec=8.0` を持っており影響を受けない。UI ラベルで対象範囲を明示する（「翻訳API呼び出し間隔」）。
9. **OpenAI 音声モデル互換性**: `llm_backend=openai` の通常モードでは `AUDIO_CAPABLE_MODELS` に含まれないモデルで即エラーになる可能性がある。interval 変更とは独立した既存の問題だが、テストでは `gpt-4o-audio-preview` を明示使用して回避する。

## Rollback Plan

- 各 Sprint は独立したコミットで管理
- Sprint 1 のみで十分な効果があるため、Sprint 2-3 で問題があれば Sprint 1 まで戻せる

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **[High] `api_interval` 独立キー化**: Task 1.5 を全面書き換え。`interval`（3/5/8 整数チャンク秒数）と `api_interval`（float API 間隔）を完全分離。`config.py` に独立 sanitize、移行ルール、デフォルト補完を追加
- **[High] LLM vs STT のレート制御分離**: Task 1.2 を LLM ワーカー専用に書き換え、新規 Task 1.3 として OpenAI STT の queue/backpressure 方針を独立タスク化。旧 Task 1.3/1.4 を 1.4/1.5 に繰り下げ
- **[High] VAD/continuous チャンク責務の明確化**: Task 2.1 を書き換え。短チャンク追加前に `chunk_seconds` の責務（continuous 専用 vs VAD 共用）を確定するステップを追加
- **[Medium] 既存テスト更新**: Task 4.1 に「既存 controller テストの期待値更新」と「`_make_config()` ヘルパーの影響確認」を追記
- **[Medium] 2フェーズ警告の表示先変更**: Task 3.1 をステータスバーから専用ラベルに変更。ステータスバーは `status` イベントで上書きされるため
- **[Medium] Task 3.2/3.3 マージ**: Task 3.2 と 3.3 を「ポーリング戦略の単純化」として1タスクに統合。`event_generate` 案は見送り明記
- **[Medium] Task 4.3 拡充**: `api_interval` の独立キー検証テストを7ケースに拡充（文字列変換、不正値、欠損値、相互汚染テスト）
- **Hidden risks 追加**: 停止時の未処理要求ドロップ、LLM 側 queue の silent drop、`api_interval` 適用スコープ、OpenAI 音声モデル互換性を Potential Risks & Gotchas に追記
- **VAD 0.5s 回帰テスト**: Risk 3 に「文中ポーズ 0.3-0.7s の回帰テストを Task 4.2 で追加」を明記

### Skipped Feedback
- [Low] Sprint 2 のリスク評価が強すぎる: VAD 0.5s の判断は設計方針であり、回帰テスト追加で十分に対処済み
- [Low] Prerequisites が現リポジトリ状態と噛み合わない: 情報としては正しいが、計画の実行可能性に影響しないため修正不要
- Hidden risk: idle 判定の競合 (`pending_requests`/`is_busy` のロック不足): 既存の問題でこの計画のスコープ外。別途対処が望ましいが、この計画では扱わない
- Hidden risk: SDK/provider 互換性 (`openai>=1.0` のストリーム shape 依存): この計画のスコープ外。interval 変更とは独立した既存の技術的負債
