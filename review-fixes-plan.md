# Plan: レビュー指摘事項の全面修正

**Generated**: 2026-03-07
**Estimated Complexity**: High

## Overview
コードレビューで発見された全指摘事項を修正する。優先度順に4スプリントで段階的に改善し、各スプリントで動作確認可能な状態を維持する。

**方針**:
- Python 3.11+、自分専用ツール
- 既存動作を壊さないことが最優先
- 棚卸し → セキュリティ → 堅牢性 → 品質の順

**現状**: モジュール分割、keyring化、テスト基盤、README は実装済み（`a1ddc77`〜`f0bc757`）。本計画は未解決の残課題のみを対象とする。

## Prerequisites
- Python 3.11+
- 既存の動作確認済み環境
- `pip install pytest keyring`

---

## Sprint 1: 未解決レビュー指摘の棚卸しと残課題化
**Goal**: 直近コミットで既に反映済みの項目を除外し、未解決のレビュー指摘だけを実装対象として確定する
**Demo/Validation**:
- レビュー指摘一覧に対して `done / partially done / not done` の判定が付与されている
- Sprint 2 以降のタスクが、棚卸し結果を前提に「不足分の拡張」のみへ更新されている

### Task 1.1: 既存実装の棚卸し
- **Location**: `realtime_translator/`, `translator.py`, `start.bat`, `pyproject.toml`, `tests/`, `README.md`
- **Description**: 現行ブランチを確認し、レビュー指摘に対応する既存実装を洗い出す。少なくとも以下を「実装済み」として記録する:
  - `realtime_translator/` 配下のモジュール分割（constants, audio, api, prompts, vad, config, whisper_stt, audio_utils, app, __main__）
  - `translator.py` の後方互換ラッパー
  - `start.bat` の `python -m realtime_translator` 起動
  - `pyproject.toml` の pytest 設定
  - `tests/test_prompts.py`, `tests/test_audio.py`, `tests/test_config.py` (17 tests passing)
  - `README.md`
  - `config.py` の keyring 保存・移行
  - `prompts.py` のプロンプト生成分離
  - `api.py` の `is_running` プロパティ
  - `audio_utils.py` による循環依存解消
- **Dependencies**: なし
- **Acceptance Criteria**:
  - 実装済み資産の一覧が作成されている
  - 再作業すると回帰リスクがある箇所が明記されている
- **Validation**: 対象ファイルの存在確認 + `pytest -q` 実行結果を記録

### Task 1.2: レビュー指摘のステータスマッピング
- **Location**: 計画書内のレビュー指摘一覧
- **Description**: 各レビュー指摘を `done / partially done / not done` に分類し、根拠となるコミットまたはファイルを紐付ける
- **Dependencies**: Task 1.1
- **Acceptance Criteria**: 全レビュー指摘に状態が付与されている
- **Validation**: 指摘一覧と根拠ファイル/コミットの対応表を確認

### Task 1.3: 残課題の再定義
- **Location**: Sprint 2 以降の各タスク記述
- **Description**: `partially done` と `not done` の項目だけを残課題として再定義する。既存実装済みの「新規作成」表現は削除し、「不足分の拡張」「既存実装の調整」に書き換える
- **Dependencies**: Task 1.2
- **Acceptance Criteria**: 後続Sprintが未解決項目ベースに更新されている
- **Validation**: Sprint 2 以降に「新規作成」と書かれた既存資産向けタスクが残っていないことを確認

---

## Sprint 2: セキュリティ修正（残課題のみ）
**Goal**: keyring 実装の堅牢化とフォールバック強化
**Demo/Validation**:
- keyring backend 失敗時にログ警告 + JSON フォールバックが機能すること
- 既存設定からの移行が自動で行われること

### Task 2.1: keyring フォールバックの強化
- **Location**: `realtime_translator/config.py`
- **Description**:
  - `save_api_key()` / `load_api_key()` で keyring backend 失敗（`keyring.errors.KeyringError` 等）を catch し、ログ警告を出して JSON フォールバックに切り替える
  - README の「安全に保存されます」を「可能な場合は OS 資格情報ストアに保存されます」に修正
- **Dependencies**: Task 1.3（棚卸し完了）
- **Acceptance Criteria**:
  - keyring が使えない環境でも API キーが保存・復元される
  - フォールバック時にログ警告が出力される
- **Validation**: keyring をモックで失敗させたテスト

### Task 2.2: interval 設定のスキーマ検証
- **Location**: `realtime_translator/config.py`
- **Description**: `load_config()` で interval 値の型・範囲チェックを設定層に追加。UI 層（`_load_config`）だけでなく設定読み込み時点で不正値を弾く
- **Dependencies**: Task 1.3
- **Acceptance Criteria**: 不正な interval 値が設定層で正規化される
- **Validation**: 不正値を含む JSON でのテスト

---

## Sprint 3: 堅牢性・例外処理（残課題のみ）
**Goal**: 例外の握りつぶしを修正し、デバッグ容易性を向上
**Demo/Validation**:
- エラー発生時にログファイルに記録されること
- UIにエラーが表示されること

### Task 3.1: _on_partial_start のマーク位置修正
- **Location**: `realtime_translator/app.py`
- **Description**: `index("end")` を `index("end-1c")` に変更し、ロールバック時の余分文字を防止
- **Dependencies**: Task 1.3
- **Acceptance Criteria**: 無音時のロールバックで余分な文字が残らない
- **Validation**: 無音検出→ロールバックの手動テスト + 自動テスト追加

### Task 3.2: 未使用legacy分岐の削除
- **Location**: `realtime_translator/app.py`
- **Description**: `_poll_queue` 内の `kind == "result"` 分岐と `_append_result` メソッドを削除（使用箇所がないことを確認済み）
- **Dependencies**: Task 1.3
- **Acceptance Criteria**: 未使用コードが除去される
- **Validation**: 全モードで動作確認 + `pytest -q` パス

### Task 3.3: キューあふれの仕様明文化
- **Location**: `README.md`, `realtime_translator/api.py`
- **Description**: `API_QUEUE_MAXSIZE = 3` による古いリクエスト破棄がバグではなく仕様であることを README とコード内コメントに明記。長発話・API 遅延時の翻訳欠落が発生し得ることをユーザーに伝える
- **Dependencies**: Task 1.3
- **Acceptance Criteria**: キューあふれ動作がドキュメント化されている
- **Validation**: README の該当セクション確認

---

## Sprint 4: 品質・テスト（既存資産の拡張）
**Goal**: 既存テストスイートの不足分を補強し、未カバー条件を追加
**Demo/Validation**:
- `pytest -q` が全件パス
- 新規追加テストがカバーする条件が明確

### Task 4.1: pytest 基盤の既存設定点検と不足分拡張
- **Location**: `tests/`, `pyproject.toml`, `.gitignore`
- **Description**:
  - 既存の pytest 設定とテスト配置を前提に、収集ルール・共通fixture・マーカー定義など不足分のみ追加する
  - 既存テスト (`tests/test_prompts.py`, `tests/test_audio.py`, `tests/test_config.py`) の実行前提を崩さないことを確認する
- **Dependencies**: なし
- **Acceptance Criteria**: 既存テストスイートが継続して収集・実行でき、追加した設定が既存挙動を壊さない
- **Validation**: `pytest -q`, `pytest --co`

### Task 4.2: 既存プロンプトテストの拡張
- **Location**: `tests/test_prompts.py`
- **Description**:
  - 既存の `tests/test_prompts.py` を拡張し、不足ケースを追加する
  - モード差分、コンテキスト注入、SILENCE_SENTINEL の正確な埋め込みなど未カバー条件を補強
- **Dependencies**: Task 4.1
- **Acceptance Criteria**: 既存ケースを維持したまま、不足していたプロンプト分岐の検証が追加されている
- **Validation**: `pytest tests/test_prompts.py -q`

### Task 4.3: 既存オーディオテストの拡張
- **Location**: `tests/test_audio.py`
- **Description**:
  - 既存テストを拡張し、`is_silent_pcm` の境界値と `VoiceActivityDetector` のフォールバック条件を追加で検証する
  - サンプルレート差異（44.1kHz で webrtcvad が RMS フォールバックになる件）の挙動テストを含む
- **Dependencies**: Task 4.1
- **Acceptance Criteria**: 無音判定とフォールバック判定の未カバー境界値がテスト化されている
- **Validation**: `pytest tests/test_audio.py -q`

### Task 4.4: 既存設定テストの拡張
- **Location**: `tests/test_config.py`
- **Description**:
  - 既存テストを拡張し、keyring フォールバック、interval スキーマ検証、移行フローの未検証ケースを追加する
  - Task 2.1, 2.2 で追加したフォールバック・バリデーションの回帰テストを含む
- **Dependencies**: Task 4.1, Task 2.1, Task 2.2
- **Acceptance Criteria**: 設定保存・復元・移行・フォールバックに対する回帰防止テストが追加されている
- **Validation**: `pytest tests/test_config.py -q`

### Task 4.5: RMS計算のパフォーマンス改善
- **Location**: `realtime_translator/audio_utils.py`
- **Description**: `is_silent_pcm` のPure Pythonループを `array` モジュール + 数学的最適化に置換。外部依存（numpy）は追加しない
- **ベンチマーク基準**: 1秒分の16kHz/16bit PCM (32000バイト) を1000回反復し、現行比30%以上の改善で完了
- **Dependencies**: Task 4.3（テストで回帰確認）
- **Acceptance Criteria**: 既存テストがパスし、ベンチマーク基準を満たす
- **Validation**: テスト + ベンチマークスクリプト実行

### Task 4.6: README.md の既存内容更新・不足分追記
- **Location**: `README.md`
- **Description**:
  - 既存の `README.md` をベースに、現行実装との差分だけを更新する
  - keyring フォールバック動作、キューあふれ仕様、各モード説明の不足項目を追記する
- **Dependencies**: Sprint 2, Sprint 3 の反映対象機能完了
- **Acceptance Criteria**: 現行ブランチの実装内容と README の説明が一致する
- **Validation**: README の手順に従って新規セットアップと基本操作を確認

---

## Testing Strategy
- Sprint 1: 棚卸し結果の確認（`pytest -q` で既存17テスト全パス確認）
- Sprint 2: keyring フォールバック + interval バリデーションのテスト追加
- Sprint 3: 手動テスト + 自動テスト追加（マーク位置修正、legacy分岐削除後の回帰）
- Sprint 4: `pytest -q` 全件パス + 新規テストによるカバレッジ拡大

## Potential Risks & Gotchas
- **Gemini API レート制限の合算超過**: `listen` と `speak` で `ApiWorker` を2本立てており、両方有効時は合算で free tier 15RPM を超過し得る。Whisper モードでは `interval = 1.0` に下げるため更にリスクが高い
- **keyring backend 失敗**: `keyring` は import 可能でも backend 不備で保存/取得が失敗するケースがある。フォールバック実装が必須
- **webrtcvad のサンプルレート制限**: 8/16/32/48kHz 以外（44.1kHz等）では自動で RMS フォールバックになり、同じ「VAD有効」でも精度が変わる
- **依存バージョンの下限のみ指定**: `google-genai>=0.8.0` 等は将来の破壊的変更をそのまま拾う。特に `generate_content_stream` は SDK 変更に弱い
- **キューあふれ時の翻訳欠落**: `API_QUEUE_MAXSIZE = 3` で古い要求を無言で捨てる設計。長発話や API 遅延時に翻訳欠落が発生するが、これはバグではなく仕様
- **テストでのPyAudio依存**: PyAudioが入っていない環境でもテストが通るよう、モック or スキップ設計が必要
- **音声テストの実デバイス非依存**: `pyaudiowpatch` の loopback 列挙やデバイス名復元は環境差分が大きく、自動テストではモックベースに限定する
- **start.bat の互換性**: `python -m realtime_translator` に変更済みだが、旧 `translator.py` もラッパーとして残存

## Rollback Plan
- 各Sprintはgitコミット単位で管理
- 問題発生時は `git revert` で該当Sprintを巻き戻し可能
- 旧 `translator.py` はラッパーとして残存するため、いつでもフォールバック可能

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **Sprint 1 全面書き換え**: モジュール分割の再実装ではなく「未解決レビュー指摘の棚卸しと残課題化」に置換（[High] Sprint 1 不整合）
- **Task 1.6 の責務分割修正**: `api.py` は API 通信専用、プロンプト生成は `prompts.py` に残す構成を前提に記述修正（[High] Task 1.6 矛盾）
- **Sprint 4 タスクを「拡張」に変更**: Task 4.1〜4.4, 4.7 を「新規作成」から「既存資産の不足分拡張」に書き換え（[High] Sprint 4 不整合）
- **ステータスマッピング工程の追加**: Task 1.2 として各レビュー指摘の `done / partially done / not done` 判定を追加（[Medium] 除外工程欠如）
- **依存関係の精緻化**: Sprint 単位の粗い依存を個別タスク単位に修正（[Medium] 依存関係の曖昧さ）
- **Sprint 3 タスクに自動テスト要件追加**: 手動確認中心だった受け入れ条件に自動テスト追加を含めた（[Medium] コミット粒度の弱さ）
- **Task 4.5 にベンチマーク基準追加**: 入力サイズ・反復回数・合格ラインを数値で定義（[Medium] 性能改善タスクの未完成）
- **Task 4.6（UIポーリング間隔短縮）削除**: 既に `root.after(100, ...)` で実装済みのため除去（[Medium] 現状コード重複）
- **keyring リスクの強化**: backend 失敗、フォールバック、テスト観点をリスク欄とタスクに追加（[Low] keyring 前提の甘さ）
- **Gemini API レート制限合算リスク追加**: listen/speak 2本立て + Whisper interval=1.0 での超過リスク（Step 3 hidden risk）
- **webrtcvad サンプルレート制限追加**: 44.1kHz でのフォールバック挙動差異をリスクとテスト対象に追加（Step 3 hidden risk）
- **依存バージョン下限のみ指定のリスク追加**: SDK 破壊的変更への脆弱性を明記（Step 3 hidden risk）
- **キューあふれ仕様の明文化**: Task 3.3 として README・コード内の仕様ドキュメント化を追加（Step 3 hidden risk）
- **interval 設定のスキーマ検証**: Task 2.2 として設定層での型・範囲チェックを追加（Step 3 hidden risk）

### Skipped Feedback
- 音声デバイスの実機テスト要求: 自分専用ツールかつ自動テストでは実デバイス依存を排除する方針のため、モックベースに限定
- `test_ctx` / `rules_ctx` の NOT_FOUND 扱い: codex-review プロセスのメタ問題であり、計画内容への影響なし
