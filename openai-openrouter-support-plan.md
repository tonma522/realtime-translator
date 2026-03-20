# Plan: OpenAI + OpenRouter マルチバックエンド対応

**Generated**: 2026-03-08
**Estimated Complexity**: High

## Overview

現在Gemini専用のリアルタイム翻訳アプリに、OpenAI（専用STT API）とOpenRouter（マルチモーダルLLM）をバックエンドとして追加する。STTと翻訳LLMを独立して選択可能にし、UIに詳細設定（モデル選択・パラメータ調整）を追加する。

### 対応するバックエンドの組み合わせ

```
STT (音声→テキスト):
  1. Gemini          - 現状の phase=0 (STT+翻訳一体) or phase=1 (STTのみ)
  2. OpenAI Whisper   - 専用STT API ($0.003-0.006/分)
  3. Local Whisper    - 現状の WhisperWorker
  4. OpenRouter       - マルチモーダルChat (gemini-2.0-flash-lite等)

翻訳 LLM (テキスト→翻訳):
  1. Gemini          - 現状の ApiWorker (phase=0 or phase=2)
  2. OpenAI Chat     - GPT-4o等 via chat completions
  3. OpenRouter      - 任意モデル via chat completions (OpenAI互換)

組み合わせ例:
  - Gemini STT + Gemini翻訳 (現状)
  - OpenAI Whisper STT + Gemini翻訳 (安価STT + 無料翻訳)
  - OpenRouter STT + OpenRouter翻訳 (APIキー1つで完結)
  - Local Whisper + OpenAI翻訳 (オフラインSTT + 高品質翻訳)
```

### アーキテクチャ方針

WhisperWorkerパターンを踏襲: STTワーカーが音声→テキスト変換後、翻訳ワーカーにphase=2リクエストを投入。翻訳ワーカーはOpenAI互換のchat completions APIを使い、Gemini/OpenAI/OpenRouterを`base_url`の差し替えで統一的に扱う。

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│ AudioCapture │────→│  STT Worker  │────→│ Translation LLM  │
│ (既存)       │     │  (選択可能)   │     │ Worker (選択可能) │
└─────────────┘     └──────────────┘     └──────────────────┘
                     Gemini phase=1       Gemini ApiWorker
                     OpenAI Whisper API   OpenAI Chat
                     Local Whisper        OpenRouter Chat
                     OpenRouter multimodal
```

## Prerequisites

- Python 3.11+
- `openai` パッケージ (optional: `pip install openai`)
- 既存テスト全パス (179 tests)
- 既存のWorkerパターン・Factory Injection理解

---

## Sprint 1: OpenAI互換LLMワーカー

**Goal**: OpenAI Chat Completions APIで翻訳を行うワーカーを作成。OpenAI直接とOpenRouter両方に対応。

**Demo/Validation**:
- ユニットテストで翻訳ストリーミングが正しくUI queueに出力されることを確認
- `python -m pytest tests/test_openai_llm.py -v`

### Task 1.1: constants.pyにOpenAI/OpenRouter定数追加
- **Location**: `realtime_translator/constants.py`
- **Description**:
  - `openai` のoptional import追加 (`OPENAI_AVAILABLE` フラグ)
  - デフォルトモデル名定数: `OPENAI_CHAT_MODEL = "gpt-4o"`, `OPENROUTER_DEFAULT_MODEL = "google/gemini-2.0-flash-001"`
  - OpenRouterベースURL: `OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"`
- **Complexity**: 1
- **Dependencies**: なし
- **Acceptance Criteria**:
  - `openai` 未インストールでも `OPENAI_AVAILABLE = False` でエラーなし
  - 定数がインポート可能
- **Validation**: `python -c "from realtime_translator.constants import OPENAI_AVAILABLE; print(OPENAI_AVAILABLE)"`

### Task 1.2: OpenAiLlmWorker骨格
- **Location**: `realtime_translator/openai_llm.py` (新規)
- **Description**:
  - `OpenAiLlmWorker` クラスの骨格 — ApiWorkerと同じライフサイクル (`submit`, `signal_stop`, `join`, `stop`, `start`, `is_running`)
  - コンストラクタ: `__init__(self, ui_queue, client, min_interval_sec, label, model)`
  - `_worker_loop`, `_req_queue`, `_running` — ApiWorkerと同構造
  - キュー管理: `enqueue_dropping_oldest` + `send_stop_sentinel` (worker_utils使用)
  - レート制限: `_last_call_time` + `min_interval_sec`
  - `_call_api(req)` はスタブ (NotImplementedError or pass)
- **Complexity**: 3
- **Dependencies**: Task 1.1
- **Acceptance Criteria**:
  - start/submit/signal_stop/join/stop が動作
  - ライフサイクルテストがパス
- **Validation**: ユニットテスト (骨格のみ)

### Task 1.3: プロンプト→メッセージ変換
- **Location**: `realtime_translator/openai_llm.py` (編集)
- **Description**:
  - `ApiRequest.prompt` (文字列) を OpenAI `messages` リストに変換するヘルパー
  - `[{"role": "user", "content": [{"type": "text", "text": prompt}, ...]}]`
  - phase=0/1: audio part追加 `{"type": "input_audio", "input_audio": {"data": base64, "format": "wav"}}`
- **Complexity**: 2
- **Dependencies**: Task 1.2
- **Acceptance Criteria**: メッセージ形式が正しい
- **Validation**: ユニットテスト

### Task 1.4: ストリーミング応答処理
- **Location**: `realtime_translator/openai_llm.py` (編集)
- **Description**:
  - `_call_api` phase=0/2 の実装: `client.chat.completions.create(stream=True)`
  - `for chunk in response:` → `chunk.choices[0].delta.content`
  - UI queueメッセージフォーマットは既存と完全互換: `("partial_start", ...)`, `("partial", ...)`, `("partial_end", ...)`
  - **OpenRouter SSEコメント処理**: OpenRouterはSSEストリームにコメント行 (`:`) を含むことがある。`openai` SDK v1.xはこれを透過的に処理するが、`chunk.choices` が空リストになるケースをガード
- **Complexity**: 4
- **Dependencies**: Task 1.3
- **Acceptance Criteria**:
  - ストリーミングチャンクがUI queueに正しく出力
  - OpenRouterの空choicesチャンクでクラッシュしない
- **Validation**: ユニットテスト

### Task 1.5: phase=1→2 自己再投入
- **Location**: `realtime_translator/openai_llm.py` (編集)
- **Description**:
  - `_call_api` phase=1 の実装: 音声 + STTプロンプト → テキスト抽出
  - transcript結果をUI queueに送信後、phase=2 `ApiRequest` を自分自身に再投入
  - `SILENCE_SENTINEL` チェック
- **Complexity**: 3
- **Dependencies**: Task 1.4
- **Acceptance Criteria**: phase=1→phase=2の自動投入が機能
- **Validation**: ユニットテスト

### Task 1.6: エラーローカライズ (型ベース)
- **Location**: `realtime_translator/openai_llm.py` (編集)
- **Description**:
  - OpenAI SDK型例外のcatch: `openai.RateLimitError`, `openai.AuthenticationError`, `openai.APITimeoutError`, `openai.InternalServerError`
  - 型ベースで日本語メッセージにマッピング (文字列regexではなく)
  - `("error", stream_id, ja_msg)` をUI queueに送信
- **Complexity**: 2
- **Dependencies**: Task 1.4
- **Acceptance Criteria**: 各例外型が日本語メッセージに変換される
- **Validation**: ユニットテスト

### Task 1.7: 音声入力モデルバリデーション
- **Location**: `realtime_translator/openai_llm.py` (編集)
- **Description**:
  - `input_audio` content typeは一部モデルのみ対応:
    - **OpenAI**: GPT-4o audio系 (`gpt-4o-audio-preview`)
    - **OpenRouter**: provider prefix付きGeminiモデル等
  - `AUDIO_CAPABLE_MODELS` 定数定義 (既知の対応モデルリスト)
  - phase=0/1 でモデルが音声非対応の場合、warning logを出して `("error", ...)` をUI queueに送信
- **Complexity**: 2
- **Dependencies**: Task 1.4
- **Acceptance Criteria**: 非対応モデルでphase=0/1使用時にエラーメッセージ表示
- **Validation**: ユニットテスト

### Task 1.8: base64音声エンコードユーティリティ
- **Location**: `realtime_translator/audio_utils.py` (編集)
- **Description**:
  - `wav_to_base64(wav_bytes: bytes) -> str` 関数追加
  - WAVバイト列をbase64文字列に変換（OpenAI/OpenRouter audio input用）
- **Complexity**: 1
- **Dependencies**: なし
- **Acceptance Criteria**:
  - 有効なbase64文字列を返す
  - `base64.b64decode()` でラウンドトリップ確認
- **Validation**: ユニットテスト

### Task 1.9: OpenAiLlmWorkerのユニットテスト
- **Location**: `tests/test_openai_llm.py` (新規)
- **Description**:
  - FakeOpenAIClient (MockのchatCompletions)
  - テストケース:
    - ライフサイクル (start → submit → stop)
    - phase=0 ストリーミング翻訳
    - phase=1 STT → phase=2自動投入
    - phase=2 テキスト翻訳
    - レート制限
    - エラーハンドリング (型ベース例外 → 日本語メッセージ)
    - キュー溢れ時のdrop-oldest
    - OpenRouter SSEコメント (空choices) 処理
    - 非対応モデルでのphase=0エラー
  - 20-25テスト
- **Complexity**: 6
- **Dependencies**: Task 1.2-1.8
- **Acceptance Criteria**:
  - 全テストパス
  - ApiWorkerテスト (`test_api.py`) と同等以上のカバレッジ
- **Validation**: `python -m pytest tests/test_openai_llm.py -v`

---

## Sprint 2: OpenAI専用STTワーカー

**Goal**: OpenAI Whisper/Transcribe APIで音声→テキスト変換するワーカー。WhisperWorkerと同パターン。

**Demo/Validation**:
- ユニットテストでSTT→phase=2翻訳ルーティングを確認
- `python -m pytest tests/test_openai_stt.py -v`

### Task 2.1: OpenAI STTワーカー作成
- **Location**: `realtime_translator/openai_stt.py` (新規)
- **Description**:
  - `OpenAiSttWorker` クラス — WhisperWorkerと同じインターフェース (`submit(wav_bytes, stream_id)`, `signal_stop`, `join`, `stop`, `start`)
  - コンストラクタ: `__init__(self, api_worker_listen, api_worker_speak, ui_queue, client, model, context)`
    - `client`: OpenAI client (`openai.OpenAI`)
    - `model`: STTモデル名 (例: `"gpt-4o-mini-transcribe"`)
    - `api_worker_listen` / `api_worker_speak`: 翻訳用ApiWorker (phase=2投入先)
  - `_worker_loop`:
    1. キューから `(wav_bytes, stream_id)` を取得
    2. `client.audio.transcriptions.create(model=model, file=("audio.wav", wav_bytes, "audio/wav"))` を呼ぶ
    3. transcriptをUI queueに送信: `("transcript", stream_id, ts, text)`
    4. phase=2 `ApiRequest` を対応するApiWorkerに投入
  - キュー管理: `queue.Queue(maxsize=3)` + `enqueue_dropping_oldest`
  - エラーハンドリング: API例外 → `("error", stream_id, msg)` (日本語化)
- **Complexity**: 5
- **Dependencies**: Task 1.1
- **Acceptance Criteria**:
  - WhisperWorkerと同じライフサイクル
  - WAVバイトをOpenAI APIに正しく送信
  - transcript結果がUI queueに出力
  - phase=2が正しいApiWorkerにルーティング
- **Validation**: Sprint 2完了時にユニットテスト

### Task 2.2: OpenAI STTモデル定数
- **Location**: `realtime_translator/constants.py` (編集)
- **Description**:
  - `OPENAI_STT_MODELS` リスト: `["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]`
  - `OPENAI_STT_DEFAULT_MODEL = "gpt-4o-mini-transcribe"`
- **Complexity**: 1
- **Dependencies**: なし
- **Acceptance Criteria**: 定数がインポート可能
- **Validation**: import確認

### Task 2.3: OpenAI STTワーカーのユニットテスト
- **Location**: `tests/test_openai_stt.py` (新規)
- **Description**:
  - FakeOpenAIClient (Mock audio.transcriptions)
  - テストケース:
    - ライフサイクル (start → submit → stop)
    - STT成功 → transcript出力 + phase=2投入
    - 空transcript → スキップ
    - APIエラー → エラーメッセージ
    - キュー溢れ
    - stream_idルーティング (listen→api_worker_listen, speak→api_worker_speak)
  - 10-15テスト
- **Complexity**: 4
- **Dependencies**: Task 2.1
- **Acceptance Criteria**: 全テストパス
- **Validation**: `python -m pytest tests/test_openai_stt.py -v`

---

## Sprint 3: Controller 設定整合性とバックエンド組み合わせ制約

**Goal**: `StartConfig`・Controller・UI でバックエンド組み合わせ制約を一貫して定義し、`stt_backend="openrouter"` を `llm_backend="openrouter"` の場合にのみ有効化する。

**Demo/Validation**:
- ユニットテストで各バックエンド組み合わせの起動・停止を確認
- `stt_backend="openrouter"` + `llm_backend="gemini"` で `ValueError` を確認
- `python -m pytest tests/test_controller.py -v`

### Task 3.1: StartConfig拡張
- **Location**: `realtime_translator/controller.py` (編集)
- **Description**:
  - `StartConfig` に新フィールド追加:
    ```python
    stt_backend: str = "gemini"        # "gemini" | "openai_whisper" | "whisper" | "openrouter"
    llm_backend: str = "gemini"        # "gemini" | "openai" | "openrouter"
    openai_api_key: str = ""           # OpenAI直接 or OpenRouter用
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    openai_chat_model: str = "gpt-4o"
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.0-flash-001"
    ```
- **Complexity**: 2
- **Dependencies**: なし
- **Acceptance Criteria**: 後方互換 (デフォルト値でGemini動作)
- **Validation**: 既存テストが全パス

### Task 3.2: Controllerバリデーション拡張
- **Location**: `realtime_translator/controller.py` (編集)
- **Description**:
  - **既存の無条件チェックを条件付きに変更**:
    - `GENAI_AVAILABLE` チェック → `llm_backend == "gemini"` or `stt_backend == "gemini"` の場合のみ
    - `config.api_key` 必須チェック → 上記と同条件でのみ
  - 新バリデーション追加:
    - `llm_backend == "openai"` → `OPENAI_AVAILABLE` チェック + `openai_api_key` 必須
    - `llm_backend == "openrouter"` → `OPENAI_AVAILABLE` チェック + `openrouter_api_key` 必須
    - `stt_backend == "openai_whisper"` → `OPENAI_AVAILABLE` チェック + `openai_api_key` 必須
    - `stt_backend == "openrouter"` → `OPENAI_AVAILABLE` チェック + `openrouter_api_key` 必須 + **`llm_backend == "openrouter"` 必須** (OpenRouter STTはLLMワーカーのphase=0で処理するため、LLMもOpenRouterでなければならない)
    - `stt_backend == "whisper"` → `WHISPER_AVAILABLE` チェック (既存)
  - エラーメッセージは日本語
  - **部分起動失敗のロールバック**: `start()` でワーカーA起動後にワーカーBが例外で失敗した場合、既に起動したワーカーAを `stop()` してから例外を再送出
- **Complexity**: 3
- **Dependencies**: Task 3.1
- **Acceptance Criteria**: 不正な組み合わせでValuError
- **Validation**: ユニットテスト

### Task 3.3: Controllerワーカー生成ロジック
- **Location**: `realtime_translator/controller.py` (編集)
- **Description**:
  - `start()` の翻訳ワーカー生成を `llm_backend` で分岐:
    ```python
    if config.llm_backend == "gemini":
        client = self._client_factory(config.api_key)
        worker_factory = self._api_worker_factory  # 既存ApiWorker
    elif config.llm_backend == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=config.openai_api_key)
        worker_factory = OpenAiLlmWorker
    elif config.llm_backend == "openrouter":
        from openai import OpenAI
        client = OpenAI(api_key=config.openrouter_api_key, base_url=OPENROUTER_BASE_URL)
        worker_factory = OpenAiLlmWorker  # 同じワーカー、base_urlが違うだけ
    ```
  - STTワーカー生成を `stt_backend` で分岐:
    ```python
    if stt_backend == "openai_whisper":
        from openai import OpenAI
        stt_client = OpenAI(api_key=config.openai_api_key)
        self._openai_stt_worker = OpenAiSttWorker(
            api_worker_listen, api_worker_speak, ui_queue, stt_client,
            model=config.openai_stt_model, context=config.context)
    elif stt_backend == "openrouter":
        # OpenRouterマルチモーダル → LLMワーカーのphase=0で処理
        pass  # 通常のon_audio_chunk()でLLMワーカーに直接投入
    elif stt_backend == "whisper":
        # 既存WhisperWorker
    elif stt_backend == "gemini":
        # 既存のphase=0 or phase=1+2ロジック
    ```
  - コールバックルーティングの分岐 (audio → 適切なSTTワーカー)
    - `stt_backend == "openai_whisper"` → `OpenAiSttWorker.submit(wav, sid)`
    - `stt_backend == "whisper"` → `WhisperWorker.submit(wav, sid)` (既存)
    - `stt_backend in ("gemini", "openrouter")` → `on_audio_chunk(wav, sid)` (LLMワーカーに直接)
  - **モデル名のワーカーへの受け渡し**:
    - OpenAiLlmWorkerにはコンストラクタで `model` パラメータを渡す
    - GeminiのApiWorkerにも `model` パラメータ追加 (Task 5.2 → Sprint 3に前倒し)
  - **`stop()` メソッドの更新**: `self._openai_stt_worker` を signal → join の2フェーズシャットダウンに追加
  - **`on_audio_chunk()` のOpenRouter対応**: `llm_backend != "gemini"` の場合、`ApiRequest.wav_bytes` にbase64変換は不要（OpenAiLlmWorker._call_apiで内部変換）
  - Factory型エイリアス追加: `OpenAiSttWorkerFactory`, `OpenAiLlmWorkerFactory`
  - **`_openai_client_factory`** をコンストラクタパラメータに追加（テスト時のDI用）
- **Complexity**: 8
- **Dependencies**: Task 1.2-1.8, 2.1, 3.1, 3.2
- **Acceptance Criteria**:
  - Gemini-only設定で既存動作を維持
  - OpenAI STT + Gemini翻訳が正しく配線
  - OpenRouter STT+翻訳が正しく配線
  - 2-phase shutdownが全バックエンドで機能（openai_stt_worker含む）
  - `openai` 未インストール + Gemini選択で正常動作
- **Validation**: ユニットテスト

### Task 3.4: Geminiモデル選択対応 (旧Task 5.2)
- **Location**: `realtime_translator/api.py` (編集)
- **Description**:
  - `ApiWorker.__init__` に `model: str = GEMINI_MODEL` パラメータ追加
  - `_call_api` で `self._model` を使用（ハードコードの `GEMINI_MODEL` を置換）
  - `_GENERATE_CONFIG` (ThinkingConfig) はモデル名に `2.5` が含まれる場合のみ適用
  - Controllerからモデル名を渡す: `StartConfig.gemini_model` → `ApiWorker(model=...)`
- **Complexity**: 3
- **Dependencies**: Task 3.1
- **Acceptance Criteria**:
  - デフォルト動作維持 (`gemini-2.5-flash`)
  - Thinking無しモデル (gemini-2.0-flash等) で `_GENERATE_CONFIG` が適用されない
  - `StartConfig.gemini_model` フィールド追加
- **Validation**: ユニットテスト

### Task 3.5: Controller拡張のユニットテスト
- **Location**: `tests/test_controller.py` (編集)
- **Description**:
  - 既存テストが全パスすることを確認（後方互換）
  - 新テストケース追加:
    - OpenAI STT + Gemini翻訳の起動・停止
    - OpenRouter STT+翻訳の起動・停止
    - OpenAI翻訳の起動・停止
    - バリデーションエラー（APIキー未設定、openai未インストール等）
    - 混合バックエンド (OpenAI STT + OpenRouter翻訳)
    - `openai` 未インストール + Gemini選択で正常起動
    - 混合バックエンドでのshutdown順序 (STT→翻訳LLM)
    - Geminiモデル切替 (thinking有無で動作変化)
  - 12-18テスト追加
- **Complexity**: 6
- **Dependencies**: Task 3.3, 3.4
- **Acceptance Criteria**: 全テストパス (既存 + 新規)
- **Validation**: `python -m pytest tests/test_controller.py -v`

---

## Sprint 4: UI拡張

**Goal**: バックエンド選択UI、APIキー管理、モデル選択、パラメータ調整を追加。

**Demo/Validation**:
- アプリ起動し、各バックエンド選択→APIキー入力→モデル選択→翻訳開始が可能
- 設定保存/復元が機能

### Task 4.1: API設定フレーム再設計
- **Location**: `realtime_translator/app.py` (編集)
- **Description**:
  - 「API設定」LabelFrameを拡張:
    - **STTバックエンド選択**: Combobox `["Gemini (内蔵)", "OpenAI Whisper", "ローカルWhisper", "OpenRouter"]`
    - **翻訳LLMバックエンド選択**: Combobox `["Gemini", "OpenAI", "OpenRouter"]`
    - **Gemini APIキー**: 既存Entry (STTかLLMでGemini選択時に表示)
    - **OpenAI APIキー**: 新Entry (STTかLLMでOpenAI選択時に表示)
    - **OpenRouter APIキー**: 新Entry (STTかLLMでOpenRouter選択時に表示)
  - バックエンド選択変更時にAPIキー入力欄の表示/非表示を切替 (`trace_add`)
  - 既存のWhisper設定チェックボックスは `stt_backend == "whisper"` 時のみ表示
- **Complexity**: 6
- **Dependencies**: Sprint 3完了
- **Acceptance Criteria**:
  - バックエンド切替で適切なAPIキー欄が表示
  - 不要なフィールドは非表示
  - 既存Gemini-only操作が壊れない
- **Validation**: アプリ起動して目視確認

### Task 4.2: モデル選択UI
- **Location**: `realtime_translator/app.py` (編集)
- **Description**:
  - **Geminiモデル選択**: Combobox `["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-2.5-pro"]`
  - **OpenAI STTモデル選択**: Combobox `["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]`
  - **OpenAI Chatモデル選択**: Combobox `["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"]`
  - **OpenRouterモデル選択**: Entry (自由入力) + よく使うモデルのプリセットCombobox
  - 各Comboboxはバックエンド選択に応じて表示/非表示
- **Complexity**: 4
- **Dependencies**: Task 4.1
- **Acceptance Criteria**:
  - 選択したモデルがStartConfigに反映
  - バックエンドに応じて適切なモデル一覧が表示
- **Validation**: アプリ起動して目視確認

### Task 4.3: StartConfig組み立て更新
- **Location**: `realtime_translator/app.py` (編集)
- **Description**:
  - `_start_inner()` でUI変数からStartConfigの新フィールドを収集:
    - `stt_backend`, `llm_backend`
    - `openai_api_key`, `openrouter_api_key`
    - `openai_stt_model`, `openai_chat_model`, `openrouter_model`
  - Geminiモデル選択をStartConfigに追加（現在はconstants.pyで固定）
- **Complexity**: 3
- **Dependencies**: Task 4.1, 4.2, Sprint 3
- **Acceptance Criteria**: 全UI選択がStartConfigに正しくマッピング
- **Validation**: ログで確認

### Task 4.4: 設定永続化拡張
- **Location**: `realtime_translator/app.py` (編集), `realtime_translator/config.py` (編集)
- **Description**:
  - `_save_config` / `_load_config` に新フィールド追加:
    - `stt_backend`, `llm_backend`
    - `openai_stt_model`, `openai_chat_model`, `openrouter_model`
    - `gemini_model`
  - APIキー保存: `config.py` の `save_config` でkeyring使用
    - サービス名を分離: `realtime-translator` (Gemini), `realtime-translator-openai`, `realtime-translator-openrouter`
  - 後方互換: 旧設定ファイルでもデフォルト値で読み込み可能
- **Complexity**: 4
- **Dependencies**: Task 4.3
- **Acceptance Criteria**:
  - 設定保存→アプリ再起動→設定復元
  - 旧設定ファイルで起動してもエラーなし
- **Validation**: アプリで設定保存→再起動→値が復元されることを確認

### Task 4.5: 2フェーズ/PTT/VAD UIの整合性
- **Location**: `realtime_translator/app.py` (編集)
- **Description**:
  - STTバックエンドが `"openai_whisper"` or `"whisper"` の場合:
    - 2フェーズチェックボックスを非表示（STTは外部、翻訳はphase=2固定）
  - STTバックエンドが `"openrouter"` の場合:
    - 2フェーズチェックボックスを表示（OpenRouterでphase=1可能）
  - Geminiの場合: 現状通り全オプション表示
  - `trace_add` でバックエンド変更時に自動更新
- **Complexity**: 3
- **Dependencies**: Task 4.1
- **Acceptance Criteria**: 矛盾する設定の組み合わせが不可能
- **Validation**: UI操作で確認

---

## Sprint 5: 統合テスト・デバッグ・クリーンアップ

**Goal**: 全バックエンド組み合わせの統合テスト、エッジケース対応、コード品質改善。

**Demo/Validation**:
- `python -m pytest tests/ -v` で全テストパス
- 各バックエンド組み合わせでアプリが動作

### Task 5.1: 統合テスト
- **Location**: `tests/test_integration.py` (編集)
- **Description**:
  - E2Eテスト追加:
    - OpenAI STT → Gemini翻訳 パイプライン
    - OpenRouter STT+翻訳 パイプライン
    - OpenAI STT → OpenRouter翻訳 パイプライン
    - バックエンド切替 (stop → 設定変更 → start)
  - 全バックエンドでshutdownが正しく完了
  - 4-6テスト追加
- **Complexity**: 5
- **Dependencies**: Sprint 1-4全完了
- **Acceptance Criteria**: 統合テスト全パス
- **Validation**: `python -m pytest tests/test_integration.py -v`

### Task 5.2: pyproject.toml optional依存定義
- **Location**: `pyproject.toml` (編集)
- **Description**:
  - `[project.optional-dependencies]` に `openai` extras追加:
    ```toml
    [project.optional-dependencies]
    openai = ["openai>=1.0"]
    all = ["openai>=1.0"]
    ```
  - `requirements.txt` は既存のまま変更しない（Geminiのみの最小構成として維持）
  - READMEにインストール方法を追記: `pip install -e ".[openai]"`
- **Complexity**: 2
- **Dependencies**: なし
- **Acceptance Criteria**:
  - `pip install -e .` で既存環境が壊れない (openaiは入らない)
  - `pip install -e ".[openai]"` で openai が追加される
- **Validation**: clean環境でインストール確認

### Task 5.3: デバッグログ整理
- **Location**: `realtime_translator/api.py`, `realtime_translator/openai_llm.py`, `realtime_translator/openai_stt.py`
- **Description**:
  - Sprint前半で追加したデバッグログ (`chunk #N`, `candidates=...`) を `logging.debug` で残す
  - `__main__.py` のログレベルを `logging.ERROR` に戻す
  - 各ワーカーに `[label]` プレフィックス付きログを統一
- **Complexity**: 2
- **Dependencies**: Sprint 5全体の最後
- **Acceptance Criteria**: 通常運用でログが冗長にならない
- **Validation**: アプリ起動してコンソール出力確認

---

## Testing Strategy

### ユニットテスト (Sprint 1-3)
- `test_openai_llm.py`: OpenAiLlmWorker (20-25テスト, SSEコメント・モデルバリデーション含む)
- `test_openai_stt.py`: OpenAiSttWorker (10-15テスト)
- `test_controller.py`: 既存 + マルチバックエンド + 組み合わせ制約 (12-18テスト追加)
- 合計: 42-58テスト追加

### 統合テスト (Sprint 5)
- `test_integration.py`: バックエンド組み合わせE2E (4-6テスト追加)

### 手動テスト
- 各バックエンドで実際にAPI呼び出し→翻訳が表示されることを確認
- バックエンド切替がスムーズに動作
- 設定保存/復元

### モック戦略
```python
# OpenAI Client Mock
class FakeOpenAIClient:
    class audio:
        class transcriptions:
            @staticmethod
            def create(**kwargs):
                return MagicMock(text="transcribed text")

    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                return iter([FakeChunk("translated"), FakeChunk(" text")])
```

---

## Potential Risks & Gotchas

### 1. OpenAI SDKバージョン互換性
- **リスク**: `openai` パッケージのAPIが変更される可能性
- **軽減**: SDK v1.x系を前提。`OPENAI_AVAILABLE` フラグで未インストール時はgraceful degradation

### 2. 音声フォーマット互換性
- **リスク**: OpenAI Whisper APIはWAVを受け付けるが、ファイルサイズ制限(25MB)あり
- **軽減**: 3-8秒チャンクなら数百KBなので問題なし。大きすぎる場合はエラーメッセージ表示

### 3. OpenRouterのマルチモーダル音声対応
- **リスク**: OpenRouterの `input_audio` 形式がSDKバージョンで変わる可能性
- **軽減**: base64 + JSONフォーマットは安定。SDKの `content` リスト形式で送信

### 4. APIキー管理の複雑化
- **リスク**: 3つのAPIキーをkeyringで管理→キー名衝突
- **軽減**: サービス名をプロバイダごとに分離 (`realtime-translator-gemini`, `-openai`, `-openrouter`)

### 5. UI複雑化によるUXの劣化
- **リスク**: 選択肢が多すぎてユーザーが混乱
- **軽減**: デフォルト値で現状動作を維持。バックエンド選択で関連UIのみ表示

### 6. Thinking ModelのThinkingConfig
- **リスク**: gemini-2.5-flash以外のモデルでThinkingConfig(thinkingBudget=0)がエラーになる可能性
- **軽減**: Task 5.2でモデル名に応じてconfig適用を分岐

### 7. レート制限の差異
- **リスク**: Gemini無料枠(15RPM)、OpenAI(RPM制限)、OpenRouter(20RPM free)で制限が異なる
- **軽減**: `min_interval_sec` をバックエンドごとに設定可能にする

### 8. OpenRouter phase=0 音声入力の対応モデル制限
- **リスク**: `input_audio` content typeは一部モデルのみ対応。非対応モデルでphase=0を使うとエラー
- **軽減**: OpenRouter STTバックエンド選択時、UIにモデル対応状況の注意書きを表示。phase=0非対応時は自動的にOpenAI Whisper STT + OpenRouter翻訳に切替を推奨

### 9. ApiRequest.promptの解釈差異
- **リスク**: `ApiRequest.prompt` はGeminiでは単一文字列content、OpenAIではmessages配列の一部として使用。暗黙的な多態性
- **軽減**: OpenAiLlmWorker._call_api内で明示的に変換。ApiRequestの構造自体は変更しない（既存互換性維持）

### 10. 同一APIキーでSTT+LLM同時使用時のレート制限
- **リスク**: OpenAI STT + OpenAI翻訳で同じAPIキーを使うと、合算でRPM制限に引っかかりやすい
- **軽減**: STTワーカーとLLMワーカーの`min_interval_sec`を個別設定。ドキュメントで注意喚起

### 11. 停止時のセンチネル投入失敗とAPI課金
- **リスク**: `send_stop_sentinel()` はキュー満杯時に `put_nowait(None)` が失敗し黙殺される。ワーカーが停止指示後に取り出済みリクエストを実行し、有料API課金が発生する
- **軽減**: 停止中リクエスト破棄方針を明文化。`_running` フラグを `_call_api` 冒頭でもチェック

### 12. 停止/再開時のstale UIイベント混入
- **リスク**: ストリーミング応答が遅延してUI queueに停止後のイベントが届き、再開時に前回セッションの断片が表示される
- **軽減**: `session_id` or `run_id` でUIイベントを世代分離。UI側で現在セッション以外のイベントを無視

### 13. SAMPLE_WIDTH_BYTESは16-bit PCM前提
- **リスク**: 定数化したが `array('h')`, WebRTC VAD, WAV化すべてが16-bit PCM固定。値を変更すると全系統が壊れる
- **軽減**: この定数はフォーマット抽象化ではなく重複排除。OpenAI/OpenRouter対応で音声前処理を触る際はこの制約を意識

### 14. 設定スキーマ移行での旧api_key誤復元
- **リスク**: 旧設定の単一 `api_key` がどのbackendに対応するか曖昧。Gemini用キーをOpenAI欄に誤復元する可能性
- **軽減**: `config_version` フィールド追加と明示的マイグレーション。旧設定の `api_key` は `gemini_api_key` として復元

### 15. Provider混在時のクライアント共有問題
- **リスク**: listen/speakワーカーに同一clientインスタンスを渡すと、`base_url`やheadersが片側設定でもう片側に漏れる
- **軽減**: バックエンドごとに独立したclientインスタンスを生成。共有しない

### 16. optional dependency importガード
- **リスク**: `import openai` を直接足すとOpenAI未導入環境でアプリ起動・既存テストが即死。`constants.py` の既存パターン (`try/except ImportError`) に合わせる必要がある
- **軽減**: 全OpenAI importは `constants.py` の import guard 経由。`OPENAI_AVAILABLE` で分岐

### 17. Tkinter UIテストのheadless不安定性
- **リスク**: Sprint 4でUIテストを増やすと、headless環境やCIで `tk.Tk()` 初期化が失敗/flaky
- **軽減**: UIテストはwidget組み立ての純ロジックを分離して単体テスト。Tk本体は最小限のsmokeテストに留める

---

## Rollback Plan

- 各SprintはGit上で独立したコミット群
- Sprint 1-2は新ファイル追加のみ → 削除するだけでロールバック
- Sprint 3のcontroller変更はデフォルト値で後方互換 → `stt_backend="gemini"`, `llm_backend="gemini"` で現状動作
- Sprint 4のUI変更が最もリスク高い → UIのdiffを慎重にレビュー
- 最悪の場合: `git revert` で各Sprint単位でロールバック可能

---

## File Impact Summary

| ファイル | 変更 | Sprint |
|---|---|---|
| `realtime_translator/constants.py` | 編集 | 1, 2 |
| `realtime_translator/openai_llm.py` | **新規** | 1 |
| `realtime_translator/audio_utils.py` | 編集 | 1 |
| `realtime_translator/openai_stt.py` | **新規** | 2 |
| `realtime_translator/controller.py` | 編集 | 3 |
| `realtime_translator/app.py` | 編集 | 4 |
| `realtime_translator/config.py` | 編集 | 4 |
| `realtime_translator/api.py` | 編集 | 3 |
| `tests/test_openai_llm.py` | **新規** | 1 |
| `tests/test_openai_stt.py` | **新規** | 2 |
| `tests/test_controller.py` | 編集 | 3 |
| `tests/test_integration.py` | 編集 | 5 |
| `pyproject.toml` | 編集 | 5 |

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **[High] Task 1.2 分割**: 単一の大タスクを6つのサブタスク (1.2-1.7) に分割。骨格→プロンプト変換→ストリーミング→phase=1→2→エラーローカライズ→モデルバリデーション。各タスクが独立してコミット・テスト可能
- **[High] stt_backend="openrouter" 制約**: Sprint 3のGoal・バリデーション (Task 3.2) に `llm_backend="openrouter"` 必須制約を追加
- **[High] OpenRouter SSEコメント処理**: Task 1.4 にSSEストリームの空choices チャンクガードを追加
- **[High] pyproject.toml extras**: Task 5.2 を `requirements.txt` 編集から `pyproject.toml` の `[project.optional-dependencies]` に変更
- **[High] 音声入力モデル行列**: Task 1.7 として `AUDIO_CAPABLE_MODELS` 定数と phase=0/1 バリデーションを追加
- **[Medium] start() 部分起動失敗ロールバック**: Task 3.2 に部分起動時のcleanup方針を追加
- **停止時センチネル投入失敗リスク** (Risk 11): `_running` フラグの `_call_api` 冒頭チェックで軽減
- **stale UIイベント混入** (Risk 12): session_id によるイベント世代分離を提案
- **SAMPLE_WIDTH_BYTES 制約** (Risk 13): 16-bit PCM 前提の明文化
- **設定スキーマ移行** (Risk 14): config_version + 旧api_keyのgemini_api_keyへのマイグレーション
- **Provider混在時のclient分離** (Risk 15): バックエンドごとの独立clientインスタンス生成
- **optional dependency importガード** (Risk 16): constants.py の既存 try/except ImportError パターン踏襲
- **Tkinter UIテスト不安定性** (Risk 17): widget純ロジック分離テストの方針

### Skipped Feedback
- [Medium] OpenAI STT language/prompt/timeoutパラメータ: Sprint 2の基本実装完了後に追加パラメータ対応を検討。初回スコープでは不要
- [Medium] config.py keyring provider-awareness: Task 4.4で既にサービス名分離を計画済み。追加タスク不要
- [Low] Config load fallback for missing dependencies: 既存の `dict.get()` デフォルト値パターンで十分対応済み
