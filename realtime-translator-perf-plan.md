---
# Plan: リアルタイム翻訳 高速化・精度改善 全アイデア実装

**Generated**: 2026-03-05
**Estimated Complexity**: High
**Target File**: `translator.py` (単一ファイル構成を維持)

## Overview

現在の翻訳パイプライン（音声→Gemini 1回で文字起こし＋翻訳）を5スプリントで改善する。
各スプリントは独立して動作確認可能な状態で完了する。

```
現状:
  AudioCapture → ApiWorker(共有) → Gemini(STT+翻訳,一括) → UI(一括表示)

最終形:
  AudioCapture+VAD → WhisperLocal(STT) → ApiWorker_listen → Gemini(翻訳,ストリーミング) → UI(逐次)
                                       → ApiWorker_speak  →
```

## Prerequisites

- `pip install faster-whisper`   # Sprint 5、事前インストール可
- `pip install webrtcvad-wheels` # Sprint 2、Windows対応ビルド済み
- 既存: pyaudiowpatch, google-genai, tkinter

---

## Sprint 1: 独立 ApiWorker (Idea A)

**Goal**: listen/speak が独立したレート制限で動作し互いに干渉しない
**Demo**: 両ストリーム同時有効化し、一方がAPIコール中でも他方が即座に処理される

### Task 1.1: _api_worker を2本に分割

- **Location**: `translator.py` L284-286, L483-484, L532, L543
- **Complexity**: 3
- **変更内容**:
  - `self._api_worker` を廃止
  - `self._api_worker_listen`, `self._api_worker_speak` の2本に分割
  - `_start_inner`: ストリームごとに独立した ApiWorker を生成・start
  - `_stop`: 両ワーカーを停止
  - `_on_audio_chunk`: stream_id でルーティング
- **Before**:
  ```python
  self._api_worker.submit(ApiRequest(wav_bytes, build_prompt(stream_id, context), stream_id))
  ```
- **After**:
  ```python
  worker = self._api_worker_listen if stream_id == "listen" else self._api_worker_speak
  worker.submit(ApiRequest(wav_bytes, build_prompt(stream_id, context), stream_id))
  ```
- **Acceptance Criteria**:
  - listen と speak が独立した4秒タイマーで動作
  - 片方処理中でも他方がブロックされない
- **Validation**: `logging.DEBUG` で両ストリームのタイムスタンプが重複して記録される

---

## Sprint 2: VAD による発話境界最適化 (Idea E)

**Goal**: 固定チャンクではなく発話終了を検出して送信。文が途中で切断されない
**Demo**: 1文を話してチャンク間隔（5秒）より早く自動送信される

### Task 2.1: VoiceActivityDetector クラスの追加

- **Location**: `translator.py` L72付近（AudioCapture直前）
- **Complexity**: 4
- **変更内容**:
  ```python
  try:
      import webrtcvad
      WEBRTCVAD_AVAILABLE = True
  except ImportError:
      webrtcvad = None
      WEBRTCVAD_AVAILABLE = False

  class VoiceActivityDetector:
      FRAME_MS = 30  # webrtcvad は 10/20/30ms フレームのみ対応
      def __init__(self, sample_rate: int, aggressiveness: int = 2):
          self._sr = sample_rate
          self._frame_bytes = int(sample_rate * self.FRAME_MS / 1000) * 2
          self._vad = webrtcvad.Vad(aggressiveness) if WEBRTCVAD_AVAILABLE else None
      def is_speech(self, pcm_bytes: bytes) -> bool:
          # 非対応サンプルレート(44.1kHz等)は RMS フォールバック
          if self._vad is None or self._sr not in (8000, 16000, 32000, 48000):
              return not AudioCapture._is_silent_pcm([pcm_bytes])
          frame = pcm_bytes[:self._frame_bytes]
          return len(frame) >= self._frame_bytes and self._vad.is_speech(frame, self._sr)
  ```

### Task 2.2: AudioCapture に VAD モードを追加

- **Location**: `translator.py` L77(__init__), L118(_record_loop)
- **Complexity**: 5
- **Dependencies**: Task 2.1
- **変更内容**:
  - `use_vad: bool = False` パラメータ追加
  - `silence_threshold: int = SILENCE_RMS_THRESHOLD` パラメータ追加（マイク用に高い値を設定可能）
  - VAD ON 時: 発話終了後 0.8 秒無音で送信トリガー
  - 最大 `chunk_seconds * 2` フレームを超えたら強制送信（無限待ち防止）
  - 無音判定を `self._silence_threshold` で行うよう変更（既存の連続モードも含む）

### Task 2.3: UI に VAD トグルを追加

- **Location**: `translator.py` L347（チャンク間隔フレーム）
- **Complexity**: 2
- **Dependencies**: Task 2.2
- **変更内容**:
  - `self._vad_var = tk.BooleanVar(value=True)` 追加
  - チェックボックス「VAD（発話検出）」追加
  - VAD ON 時: チャンク間隔ラジオボタンをグレーアウト
  - `_save_config` / `_load_config` に `vad_enabled` キー追加

---

## Sprint 3: ストリーミングレスポンス逐次表示 (Idea B)

**Goal**: Gemini 応答をストリームで受け取り翻訳テキストが文字単位でリアルタイム表示
**Demo**: API コール後、結果がタイプライターのように表示される

### Task 3.1: ApiWorker._call_api をストリーミングに変更

- **Location**: `translator.py` L238(_call_api)
- **Complexity**: 4
- **変更内容**:
  - `generate_content()` → `generate_content_stream()` に変更
  - UI キューに3種類のメッセージを追加:
    - `("partial_start", stream_id, ts)` — ヘッダ表示トリガー
    - `("partial", stream_id, text)`    — テキスト追記
    - `("partial_end", stream_id)`      — セパレータ挿入
  ```python
  started = False
  for chunk in self._client.models.generate_content_stream(
      model=GEMINI_MODEL, contents=[req.prompt, audio_part]
  ):
      text = chunk.text or ""
      if not text:
          continue
      if not started:
          self._ui_queue.put(("partial_start", req.stream_id, ts))
          started = True
      self._ui_queue.put(("partial", req.stream_id, text))
  if started:
      self._ui_queue.put(("partial_end", req.stream_id))
  ```

### Task 3.2: UI の _poll_queue をストリーミング対応に変更

- **Location**: `translator.py` L569(_poll_queue), L584(_append_result)
- **Complexity**: 5
- **Dependencies**: Task 3.1
- **変更内容**:
  - `self._stream_buffers: dict[str, dict]` で各ストリームの状態を管理
    - `{"text": "", "mark": "end"}` を保持
  - `partial_start`: ヘッダをテキストエリアに追記、マーク位置を記録
  - `partial`: テキストを末尾に追記
  - `partial_end`: `(無音)` 含む場合はマーク位置まで削除してロールバック、それ以外はセパレータ挿入
- **Acceptance Criteria**:
  - 複数ストリーム同時進行でも UI が崩れない（stream_id で独立管理）
  - エラー時は部分表示をクリーンアップ
  - `(無音)` を含む応答はロールバックして非表示

---

## Sprint 4: 2フェーズパイプライン (Idea C)

**Goal**: 原文テキストを即表示し翻訳は後追い。体感レイテンシ大幅改善
**Demo**: 原文が先に表示され、数秒後に訳文が同ブロックに追記される

### Task 4.1: STT専用・翻訳専用プロンプト関数の追加

- **Location**: `translator.py` L259付近（build_prompt近傍）
- **Complexity**: 2
- **変更内容**:
  ```python
  def build_stt_prompt(stream_id: str) -> str:
      lang = "英語" if stream_id == "listen" else "日本語"
      return (
          f"この音声を{lang}でそのまま文字起こししてください。翻訳は不要です。"
          "無音・聞き取れない場合は「(無音)」とだけ返してください。"
      )

  def build_translation_prompt(stream_id: str, context: str, transcript: str) -> str:
      src, dst_lang = ("英語", "日本語") if stream_id == "listen" else ("日本語", "英語")
      return (
          f"【コンテキスト】{context}
"
          f"次の{src}テキストを{dst_lang}に翻訳してください。
"
          f"テキスト: {transcript}
"
          "出力形式:
  訳文: (翻訳結果のみ)"
      )
  ```

### Task 4.2: ApiRequest に 2フェーズフィールドを追加

- **Location**: `translator.py` L184(ApiRequest dataclass)
- **Complexity**: 2
- **変更内容**:
  ```python
  @dataclass
  class ApiRequest:
      wav_bytes: bytes | None  # Phase2 では None
      prompt: str
      stream_id: str
      phase: int = 1           # 1=STT(音声入力), 2=翻訳(テキスト入力)
      context: str = ""        # Phase2 プロンプト生成用コンテキスト
      transcript: str = ""     # Phase2 入力テキスト
  ```

### Task 4.3: ApiWorker._call_api に 2フェーズロジックを追加

- **Location**: `translator.py` L238(_call_api)
- **Complexity**: 6
- **Dependencies**: Task 4.1, Task 4.2, Sprint 3完了
- **変更内容**:
  - Phase1: 音声→文字起こし（非ストリーミング、速度優先）
    - 完了後 `("transcript", stream_id, ts, text)` を UI キューに送信
    - Phase2 リクエストを自ワーカーのキューに投入
  - Phase2: テキスト→翻訳（ストリーミング、Sprint 3 の実装を流用）

### Task 4.4: UI に transcript 表示を追加

- **Location**: `translator.py` L366(タグ定義), L569(_poll_queue)
- **Complexity**: 4
- **Dependencies**: Task 4.3
- **変更内容**:
  - `("transcript", stream_id, ts, text)` 受信時に原文ブロックを表示
  - Phase2 の訳文はそのブロックの末尾に追記
  - `_stream_buffers` に原文マーク位置も追加

---

## Sprint 5: ローカル Whisper STT (Idea D)

**Goal**: 文字起こしをローカルWhisperに置き換え。Gemini Rate Limitを翻訳のみで消費
**Demo**: Whisper ON 時に音声APIコールがゼロ、文字起こしが即時オフライン表示

### Task 5.1: faster-whisper ラッパークラスの追加

- **Location**: `translator.py` L19付近（imports直後）
- **Complexity**: 4
- **Dependencies**: `pip install faster-whisper`
- **変更内容**:
  ```python
  try:
      from faster_whisper import WhisperModel
      WHISPER_AVAILABLE = True
  except ImportError:
      WhisperModel = None
      WHISPER_AVAILABLE = False

  class WhisperTranscriber:
      DEFAULT_MODEL = "small"
      def __init__(self, model_size: str = DEFAULT_MODEL, language: str | None = None):
          if not WHISPER_AVAILABLE:
              raise RuntimeError("faster-whisper が未インストールです")
          self._model = WhisperModel(model_size, device="auto", compute_type="int8")
          self._language = language
      def transcribe(self, wav_bytes: bytes) -> str:
          import io
          segments, _ = self._model.transcribe(
              io.BytesIO(wav_bytes),
              language=self._language,
              beam_size=1,
              vad_filter=True,  # faster-whisper 内蔵VADも有効化
          )
          return " ".join(seg.text.strip() for seg in segments)
  ```

### Task 5.2: WhisperWorker クラスの追加

- **Location**: `translator.py` L191付近（ApiWorker手前）
- **Complexity**: 5
- **Dependencies**: Task 5.1, Sprint 4完了
- **変更内容**:
  - `submit(wav_bytes: bytes, stream_id: str)` — キューに投入（サイズ3、古いものを廃棄）
  - ワーカースレッドで `WhisperTranscriber.transcribe()` を実行
  - 完了後:
    - `("transcript", stream_id, ts, text)` を UI キューに送信
    - 対応する ApiWorker に Phase2 翻訳リクエストを投入
  - コンストラクタ引数: `api_worker_listen`, `api_worker_speak`, `ui_queue`, `model_size`, `language`, `context`

### Task 5.3: UI に Whisper 設定パネルを追加

- **Location**: `translator.py` L331（コンテキストフレーム下）
- **Complexity**: 3
- **変更内容**:
  - Whisper 設定フレームを追加:
    ```
    [Whisper設定]
      [x] ローカルWhisper使用（STTをオフライン化）
      モデル: [tiny] [base] [small*] [medium]
      言語:   [自動] [ja] [en]
    ```
  - `WHISPER_AVAILABLE=False` 時は全体グレーアウト＋「pip install faster-whisper が必要」ラベル
  - `self._whisper_var = tk.BooleanVar(value=False)`
  - `self._whisper_model_var = tk.StringVar(value="small")`
  - `self._whisper_lang_var = tk.StringVar(value="auto")`
  - モデルロード中は「Whisper準備中...」をステータスバーに表示

### Task 5.4: _start_inner に Whisper/Gemini STT 分岐を追加

- **Location**: `translator.py` L458(_start_inner)
- **Complexity**: 5
- **Dependencies**: Task 5.2, Task 5.3
- **変更内容**:
  ```python
  use_whisper = self._whisper_var.get() and WHISPER_AVAILABLE
  if use_whisper:
      lang = None if self._whisper_lang_var.get() == "auto" else self._whisper_lang_var.get()
      # WhisperWorker は別スレッドでモデルをロード
      self._whisper_worker = WhisperWorker(
          api_worker_listen=self._api_worker_listen,
          api_worker_speak=self._api_worker_speak,
          ui_queue=self._ui_queue,
          model_size=self._whisper_model_var.get(),
          language=lang, context=context,
      )
      self._whisper_worker.start()
      def make_whisper_cb(sid):
          return lambda wav: self._whisper_worker.submit(wav, sid)
  else:
      # Sprint 4 の 2フェーズ Gemini パイプライン
      def make_cb(sid):
          return lambda wav: self._on_audio_chunk(wav, sid, context)
  ```
  - Whisper 使用時は `MIN_API_INTERVAL_SEC` の実効値を 1.0 秒に設定（テキスト翻訳は高速）

### Task 5.5: _stop に WhisperWorker 停止を追加

- **Location**: `translator.py` L522(_stop)
- **Complexity**: 1
- **Dependencies**: Task 5.4
- **変更内容**: `self._whisper_worker` が存在すれば `stop()` して `None` に

### Task 5.6: save/load config に Whisper 設定を追加

- **Location**: `translator.py` L622, L639
- **Complexity**: 1
- **Dependencies**: Task 5.3
- **変更内容**: `whisper_enabled`, `whisper_model`, `whisper_lang` キーを追加（後方互換あり）

---

## Testing Strategy

| Sprint | 検証方法 |
|--------|---------|
| 1 | `logging.DEBUG` で listen/speak のAPIコール時刻が独立して記録される |
| 2 | 1文を話してチャンク間隔（5秒）より早く送信される |
| 3 | 応答テキストがタイプライター式に表示される |
| 4 | 原文が先に表示され、翻訳が数秒後に同ブロックに追記される |
| 5 | Whisper ON 時に Gemini 音声コールがゼロ、文字起こしが即時表示 |

---

## Potential Risks & Gotchas

| ID | リスク | 対策 |
|----|--------|------|
| R1 | webrtcvad は 8/16/32/48kHz のみ対応。44.1kHz では動作しない | Task 2.1 に RMS フォールバック実装を含める |
| R2 | ストリーミング中の UI 更新はメインスレッド外から不可 | `_ui_queue` 経由でのみ UI 操作（現状通り） |
| R3 | 2フェーズで Phase2 が古い翻訳を届ける可能性 | キューサイズ 3 で自然廃棄 |
| R4 | faster-whisper 初回モデルロードに最大 30 秒 | 別スレッドでロード、完了まで「準備中」表示（Task 5.3） |
| R5 | Whisper medium モデルでメモリ不足 | デフォルト small 固定、UI に「要 4GB RAM」警告 |
| R6 | ストリーミング中の `(無音)` 判定 — 部分テキストでは判定不能 | `partial_end` 時に累積テキストを評価してロールバック（Task 3.2） |
| R7 | Sprint 4 の 2フェーズで API コールが 2倍消費 | Sprint 5 完了まで `listen` のみ 2フェーズ適用を検討 |
| R8 | PTT モードで Phase1 STT が完了する前に Phase2 が届く可能性 | `transcript` 表示と訳文表示の順序保証（シーケンス番号 or ロック） |

---

## Rollback Plan

- 各スプリントを独立した git commit で管理
- `use_vad=False`, `use_whisper=False` フラグで旧動作に切り替え可能
- 設定ファイルは後方互換維持（新キーはすべてデフォルト値あり）
- Sprint 4/5 は Sprint 3 以前の状態に `git revert` で戻せる

---

## Critical Fixes (Post-Review)

以下の問題を各スプリントの実装時に必ず対処すること。

### Fix C1: ストリーミング例外時に partial_end を送信 (Sprint 3 - Task 3.1)

`_call_api` のストリーミングループに `finally` 節を追加し、例外時も `partial_end` を送信してUIをクリーンアップする。

```python
started = False
try:
    for chunk in self._client.models.generate_content_stream(...):
        try:
            text = chunk.text or ""
        except ValueError:
            continue  # 安全フィルターブロック
        if not text:
            continue
        if not started:
            self._ui_queue.put(("partial_start", req.stream_id, ts))
            started = True
        self._ui_queue.put(("partial", req.stream_id, text))
except Exception as e:
    self._ui_queue.put(("error", req.stream_id, str(e)))
finally:
    if started:
        self._ui_queue.put(("partial_end", req.stream_id))
```

### Fix C2: mark を "end" 文字列ではなく frozen インデックスで保存 (Sprint 3 - Task 3.2)

```python
# partial_start 受信時
mark = self._result_text.index("end")  # "42.0" 形式のフローズンインデックス
self._stream_buffers[stream_id] = {"text": "", "mark": mark}

# partial 受信時 (累積も必須)
self._stream_buffers[stream_id]["text"] += text

# partial_end 受信時
if "(無音)" in self._stream_buffers[stream_id]["text"]:
    mark = self._stream_buffers[stream_id]["mark"]
    self._result_text.delete(mark, "end")  # ロールバック
else:
    self._result_text.insert("end", "─" * 50 + "\n", "separator")
del self._stream_buffers[stream_id]
```

### Fix C3: wav_bytes=None のガード追加 (Sprint 4 - Task 4.3)

```python
def _call_api(self, req: ApiRequest) -> None:
    ...
    if req.phase == 2:
        # Phase2: テキスト入力のみ（音声パートなし）
        contents = [req.prompt]
    else:
        # Phase1: 音声入力
        audio_part = genai_types.Part.from_bytes(data=req.wav_bytes, mime_type="audio/wav")
        contents = [req.prompt, audio_part]
    for chunk in self._client.models.generate_content_stream(model=GEMINI_MODEL, contents=contents):
        ...
```

### Fix C4: WhisperModel の初期化をワーカースレッド内で行う (Sprint 5 - Task 5.2)

`WhisperTranscriber.__init__` をワーカースレッドの `_worker_loop` 開始時に呼び出す。`__init__` ではモデルを生成せず、`start()` 後のスレッド内で初期化する。

```python
class WhisperWorker:
    def __init__(self, ...):
        self._model_size = model_size
        self._language = language
        self._transcriber: WhisperTranscriber | None = None  # スレッド内で初期化

    def _worker_loop(self):
        self._ui_queue.put(("status", "Whisper準備中..."))
        self._transcriber = WhisperTranscriber(self._model_size, self._language)
        self._ui_queue.put(("status", "Whisper準備完了"))
        while self._running:
            ...
```

### Fix C5: VoiceActivityDetector はフレーム単位で判定する (Sprint 2 - Task 2.1/2.2)

`is_speech()` は1チャンク全体ではなく 30ms フレーム単位で呼び出す。`_record_loop` の VAD ブランチでは各 `AUDIO_CHUNK_SIZE` データを 30ms フレームに分割して判定し、発話状態を追跡するステートマシンを実装する。

```python
# _record_loop VAD ブランチ
vad = VoiceActivityDetector(sample_rate)
speech_frames: list[bytes] = []
silent_frames_count = 0
SILENCE_TRIGGER = int(sample_rate * 0.8 / AUDIO_CHUNK_SIZE)  # 0.8秒分のチャンク数

for data in stream:
    is_sp = vad.is_speech(data)  # 各チャンクを個別に判定
    if is_sp:
        speech_frames.append(data)
        silent_frames_count = 0
    elif speech_frames:
        silent_frames_count += 1
        speech_frames.append(data)
        if silent_frames_count >= SILENCE_TRIGGER:
            callback(_to_wav(speech_frames, ...))
            speech_frames = []
            silent_frames_count = 0
    # 最大長チェック（強制送信）
    if len(speech_frames) * AUDIO_CHUNK_SIZE > sample_rate * chunk_seconds * 2:
        callback(_to_wav(speech_frames, ...))
        speech_frames = []
        silent_frames_count = 0
```

### Fix C6: VAD と PTT の排他制御 (Sprint 2 - Task 2.3)

- `_record_loop` の VAD ブランチは `self._ptt_event is None` の場合のみ有効にする
- UI: PTT チェックボックスが ON の場合は VAD チェックボックスを自動的にグレーアウト（逆も同様）

### Fix C7: MIN_API_INTERVAL_SEC をグローバル変更しない (Sprint 5 - Task 5.4)

`ApiWorker.__init__` に `min_interval_sec: float = MIN_API_INTERVAL_SEC` パラメータを追加し、Whisper 使用時は `ApiWorker(ui_queue, client, min_interval_sec=1.0)` として個別に設定する。グローバル定数 `MIN_API_INTERVAL_SEC` は変更しない。

### Fix C8: _stop() で _stream_buffers をリセット (Sprint 3 - Task 3.2)

```python
def _stop(self):
    ...
    # ストリーミング中の残存状態をクリア
    if hasattr(self, "_stream_buffers"):
        self._stream_buffers.clear()
```

### Fix C9: _whisper_worker を __init__ で None 初期化 (Sprint 5 - Task 5.1)

```python
# TranslatorApp.__init__ に追加
self._whisper_worker: "WhisperWorker | None" = None
```

### Fix C10: Sprint 4/5 のロールバック依存関係を明記

Sprint 4 (`ApiRequest` の変更) と Sprint 5 は独立してリバートできない。Sprint 5 をリバートする場合は Sprint 4 のコードも同時にリバートするか、`ApiRequest` の新フィールドをデフォルト値付きで維持する必要がある。ロールバック時はこの依存関係を考慮した上で `git revert` を実行する。

---

## 実装ロードマップ

```
Day 1:  Sprint 1 (独立ApiWorker)   30分、即効果
        Sprint 2 (VAD)             2時間、精度改善

Day 2:  Sprint 3 (ストリーミング)  2時間、体感速度改善

Day 3:  Sprint 4 (2フェーズ)       半日、アーキテクチャ変更

Day 4:  Sprint 5 (Whisper)         1日、最大効果
```
