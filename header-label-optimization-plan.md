# Plan: ヘッダーラベル最適化 + トークン削減 + レスポンス高速化

**Generated**: 2026-03-08
**Estimated Complexity**: Medium

## Overview

5つの軸でトークン削減とレスポンス高速化を実現する:
1. **A: プロンプト英語化**: 入力トークン ~50%削減
2. **B: コンテキスト圧縮**: 長いコンテキストの入力トークン削減
3. **D: 原文出力の条件付き廃止**: UI チェックボックスで切替（デフォルトON=原文表示）
4. **E: SILENCE_SENTINEL 短縮**: 無音時の出力トークン削減
5. **F: Phase 1 非ストリーミング化**: STTフェーズのスループット改善

加えて既存プランの:
- **UIヘッダーに言語ペア表示**
- **Phase 1/2 レート制限の分離**（最大4秒短縮）
- **UIポーリング適応化**

### 現状の表示
```
[11:22:21] PC音声→日本語
原文: The meeting starts at 10.
訳文: 会議は10時に始まります。
──────────────────────────────────────────────────
```

### 変更後の表示（原文表示OFF）
```
[11:22:21] PC音声 英語→日本語
会議は10時に始まります。
──────────────────────────────────────────────────
```

### 変更後の表示（原文表示ON — デフォルト）
```
[11:22:21] PC音声 英語→日本語
原文: The meeting starts at 10.
訳文: 会議は10時に始まります。
──────────────────────────────────────────────────
```

## Prerequisites
- 変更対象ファイルの理解（調査済み）
- 既存テスト全パス確認

## Sprint 1: プロンプト英語化 + UIヘッダー + 原文表示オプション

**Goal**: プロンプトを英語化し、UIヘッダーに言語ペア表示、「原文も表示」チェックボックスを追加

**Demo/Validation**:
- `python -m pytest tests/ -q` 全テストパス
- UI表示で言語ペアがヘッダーに出ること（目視）
- チェックボックスON/OFFでプロンプトが切り替わること

### Task 1.1: `_STREAM_META` に言語情報を追加
- **Location**: `realtime_translator/constants.py` L67-70
- **Description**: `_STREAM_META` に言語ペアを含める
- **Dependencies**: なし
- **変更内容**:
  ```python
  # 変更前
  _STREAM_META: dict[str, tuple[str, str]] = {
      "listen": ("PC音声→日本語", "stream_listen"),
      "speak":  ("マイク→英語",   "stream_speak"),
  }

  # 変更後: 3要素目に言語ペア文字列を追加
  _STREAM_META: dict[str, tuple[str, str, str]] = {
      "listen": ("PC音声", "stream_listen", "英語→日本語"),
      "speak":  ("マイク", "stream_speak",  "日本語→英語"),
  }
  ```
- **Validation**: 既存テストパス（参照箇所修正後）

### Task 1.2: `app.py` ヘッダー表示に言語ペアを含める
- **Location**: `realtime_translator/app.py` — `_on_partial_start`, `_on_transcript`
- **Description**: ヘッダーに言語情報を表示
- **Dependencies**: Task 1.1
- **変更内容**:
  ```python
  def _on_partial_start(self, stream_id: str, ts: str) -> None:
      self._flush_active_partials()
      label, tag, langs = _STREAM_META[stream_id]
      with self._editable_result():
          mark = self._result_text.index("end-1c")
          self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
      self._stream_buffers[stream_id] = {"chunks": [], "mark": mark}

  def _on_transcript(self, stream_id: str, ts: str, text: str) -> None:
      self._flush_active_partials()
      label, tag, langs = _STREAM_META[stream_id]
      with self._editable_result():
          self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
          self._result_text.insert("end", f"原文: {text}\n", "original")
  ```
- **Validation**: 既存テストパス

### Task 1.3: 「原文も表示」チェックボックスを追加
- **Location**: `realtime_translator/app.py` — `_build_ui` 内の「有効ストリーム」フレーム
- **Description**: `_show_original_var` (BooleanVar, default=True) を追加
- **Dependencies**: なし
- **変更内容**:
  ```python
  # 有効ストリームフレームに追加
  self._show_original_var = tk.BooleanVar(value=True)
  ttk.Checkbutton(stream_frame, text="原文も表示",
                  variable=self._show_original_var).pack(side="left", padx=12, pady=4)
  ```
- **config永続化にも追加**:
  - `_save_config`: `"show_original": self._show_original_var.get()`
  - `_load_config`: `self._show_original_var.set(config.get("show_original", True))`
- **Validation**: UI目視確認

### Task 1.4: `build_prompt` に `show_original` パラメータを追加 + 英語化
- **Location**: `realtime_translator/prompts.py` L5-16
- **Description**: プロンプトを英語化し、`show_original` フラグで原文出力を制御
- **Dependencies**: なし（並行可能）
- **変更内容**:
  ```python
  def build_prompt(stream_id: str, context: str, show_original: bool = True) -> str:
      """通常モード: 音声→STT+翻訳 (phase=0)"""
      src, dst = STREAM_LANGS[stream_id]
      if show_original:
          return (
              f"You are a realtime translation assistant.\n"
              f"[Context] {context}\n\n"
              f"Listen to the audio and translate {src} to {dst}.\n"
              f"Output format (strictly follow):\n"
              f"  原文: ({src} text)\n"
              f"  訳文: ({dst} text)\n\n"
              f"If silent or inaudible, reply only: {SILENCE_SENTINEL}"
          )
      return (
          f"You are a realtime translation assistant.\n"
          f"[Context] {context}\n\n"
          f"Listen to the audio and translate {src} to {dst}.\n"
          f"Output only the translation. No labels, no original text.\n\n"
          f"If silent or inaudible, reply only: {SILENCE_SENTINEL}"
      )
  ```
- **Acceptance Criteria**:
  - 英語プロンプト
  - `show_original=True`: 原文+訳文ラベル付き出力
  - `show_original=False`: 訳文のみ出力
  - 無音指示は維持
- **Validation**: `test_prompts.py` 更新後にパス

### Task 1.5: `build_translation_prompt` を英語化
- **Location**: `realtime_translator/prompts.py` L28-36
- **Description**: Phase 2 プロンプトを英語化。Phase 2 は常に訳文のみ（原文は Phase 1 で表示済み）
- **Dependencies**: なし（並行可能）
- **変更内容**:
  ```python
  def build_translation_prompt(stream_id: str, context: str, transcript: str) -> str:
      """2フェーズ Phase2: テキスト→翻訳のみ (phase=2)"""
      src, dst = STREAM_LANGS[stream_id]
      return (
          f"[Context] {context}\n"
          f"Translate the following {src} text to {dst}.\n"
          f"Text: {transcript}\n"
          "Output only the translation."
      )
  ```
- **Validation**: `test_prompts.py` 更新後にパス

### Task 1.6: `build_stt_prompt` を英語化
- **Location**: `realtime_translator/prompts.py` L19-25
- **Description**: Phase 1 STTプロンプトを英語化
- **Dependencies**: なし
- **変更内容**:
  ```python
  def build_stt_prompt(stream_id: str) -> str:
      """2フェーズ Phase1: 音声→文字起こしのみ (phase=1)"""
      src, _ = STREAM_LANGS[stream_id]
      return (
          f"Transcribe this audio in {src} exactly as spoken. No translation.\n"
          f"If silent or inaudible, reply only: {SILENCE_SENTINEL}"
      )
  ```
- **Validation**: `test_prompts.py` 更新後にパス

### Task 1.7: `StartConfig` に `show_original` フィールドを追加
- **Location**: `realtime_translator/controller.py` — `StartConfig` dataclass
- **Description**: `show_original: bool = True` フィールドを追加し、`_on_audio_chunk` で `build_prompt` に渡す
- **Dependencies**: Task 1.4
- **変更内容**:
  ```python
  @dataclass
  class StartConfig:
      ...
      show_original: bool = True

  # _on_audio_chunk 内:
  prompt=build_prompt(stream_id, self._context, self._show_original)
  ```
- **Validation**: 既存コントローラーテストパス

### Task 1.8: `app.py` の `_start_inner` で `show_original` を渡す
- **Location**: `realtime_translator/app.py` — `_start_inner`
- **Description**: StartConfig に `show_original` を追加
- **Dependencies**: Task 1.3, 1.7
- **変更内容**:
  ```python
  config = StartConfig(
      ...
      show_original=self._show_original_var.get(),
  )
  ```
- **Validation**: 既存テストパス

### Task 1.9: テスト更新
- **Location**: `tests/test_prompts.py`, `tests/test_api.py`, `tests/test_controller.py`
- **Description**: プロンプト英語化と `show_original` パラメータに合わせてテスト更新
- **Dependencies**: Task 1.4-1.8
- **テスト変更**:
  - `test_prompts.py`:
    - 英語プロンプト内容を検証（言語名は日本語のまま: "英語", "日本語"）
    - `show_original=True` → "原文:" と "訳文:" が含まれる
    - `show_original=False` → "translation" が含まれ "原文:" が含まれない
    - 「翻訳結果のみ」→ "Output only the translation" に変更
  - `test_api.py`: `build_translation_prompt` のモック確認
  - `test_controller.py`: `show_original` を `_make_config` に追加
- **Validation**: `python -m pytest tests/ -q`

## Sprint 2: SILENCE_SENTINEL 短縮 + Phase 1 非ストリーミング化

**Goal**: 無音時の出力トークン削減と、Phase 1 のスループット改善

**Demo/Validation**:
- `python -m pytest tests/ -q` 全テストパス
- 無音検出が正常動作すること

### Task 2.1: SILENCE_SENTINEL を短縮
- **Location**: `realtime_translator/constants.py` L50
- **Description**: `(無音)` → `…` に短縮
- **Dependencies**: なし
- **変更内容**:
  ```python
  # 変更前
  SILENCE_SENTINEL = "(無音)"
  # 変更後
  SILENCE_SENTINEL = "…"
  ```
- **影響範囲**: SILENCE_SENTINEL は以下で参照:
  - `prompts.py`: 全プロンプトで無音指示に使用（プロンプト内に埋め込み）
  - `api.py:151`: Phase 1 transcript にセンチネルが含まれるか判定
  - `openai_llm.py:173`: 同上
  - `openai_stt.py`: 同上
  - `whisper_stt.py`: 同上
  - `app.py:537`: `_on_partial_end` で無音削除判定
- **リスク**: `…` は通常テキストに出現しうる。ただし API 応答全体が `…` のみの場合にしかマッチしないため誤検出リスクは低い
  - 代替案: `"<silent>"` にすれば誤検出ゼロだが出力トークンは `(無音)` と同等
- **Acceptance Criteria**:
  - 全参照箇所で新センチネルが使われる（定数参照なので自動）
  - 無音時に UI から該当行が削除される動作が維持される
- **Validation**: 既存テストパス（SILENCE_SENTINEL を定数参照しているため自動追従）

### Task 2.2: Phase 1 を非ストリーミング化 (ApiWorker)
- **Location**: `realtime_translator/api.py` L130-159
- **Description**: Phase 1 は UI 表示不要（transcript は結果のみ使用）なので、ストリーミングではなくバッチ応答に切り替え
- **Dependencies**: なし
- **変更内容**:
  ```python
  if req.phase == 1:
      audio_part = genai_types.Part.from_bytes(data=req.wav_bytes, mime_type="audio/wav")
      # ストリーミング不要: バッチで応答を取得
      response = self._client.models.generate_content(
          model=self._model, contents=[req.prompt, audio_part],
          config=_generate_config_for_model(self._model),
      )
      transcript = response.text.strip() if response.text else ""
      ...
  ```
- **Acceptance Criteria**:
  - Phase 1 が `generate_content`（非ストリーミング）を使用
  - Phase 0/2 は引き続き `generate_content_stream` を使用
  - transcript の UI 送信と Phase 2 自動投入は維持
- **Validation**: 既存テストパス + Phase 1 テスト追加

### Task 2.3: Phase 1 を非ストリーミング化 (OpenAiLlmWorker)
- **Location**: `realtime_translator/openai_llm.py` L147-185
- **Description**: OpenAI 側も Phase 1 を非ストリーミングに
- **Dependencies**: Task 2.2（パターン確認）
- **変更内容**:
  ```python
  def _handle_phase1(self, req: ApiRequest, ts: str) -> None:
      messages = _build_messages(req.prompt, req.wav_bytes)
      # ストリーミング不要: バッチで応答を取得
      response = self._client.chat.completions.create(
          model=self._model, messages=messages, stream=False,
      )
      transcript = response.choices[0].message.content.strip() if response.choices else ""
      ...
  ```
- **Validation**: 既存テストパス

### Task 2.4: テスト更新
- **Location**: `tests/test_api.py`, `tests/test_openai_llm.py`
- **Description**: Phase 1 の非ストリーミング化に合わせてモック更新
- **Dependencies**: Task 2.2, 2.3
- **Validation**: `python -m pytest tests/ -q`

## Sprint 3: Phase 1/2 レート制限の分離

**Goal**: 2フェーズモードでPhase 1完了後、Phase 2が即座に実行されるようにする（最大4秒短縮）

**Demo/Validation**:
- `python -m pytest tests/ -q` 全テストパス
- 2フェーズモードでPhase 1→Phase 2間の待機が4秒→0秒になること（ログで確認）

### Task 3.1: `ApiWorker` にフェーズ別レート制限を導入
- **Location**: `realtime_translator/api.py` L69-86, L124-127
- **Description**: `_last_call_time` をフェーズ別に分離。Phase 2（テキストのみ）はPhase 0/1（音声付き）のレート制限と独立させる
- **Dependencies**: なし
- **変更内容**:
  ```python
  def __init__(self, ...):
      ...
      self._last_audio_call_time = 0.0   # phase 0, 1
      self._last_text_call_time = 0.0    # phase 2

  def _call_api(self, req: ApiRequest) -> None:
      is_text_only = req.phase == 2
      last_time = self._last_text_call_time if is_text_only else self._last_audio_call_time
      elapsed = time.monotonic() - last_time
      if elapsed < self._min_interval_sec:
          time.sleep(self._min_interval_sec - elapsed)
      ...
      now = time.monotonic()
      if is_text_only:
          self._last_text_call_time = now
      else:
          self._last_audio_call_time = now
  ```
- **Acceptance Criteria**:
  - Phase 1完了直後にPhase 2が即実行される
  - 同フェーズ間のレート制限は維持
- **Validation**: 既存テストパス + 新規テスト

### Task 3.2: `OpenAiLlmWorker` にも同様の分離を適用
- **Location**: `realtime_translator/openai_llm.py` L69-87, L130-145
- **Dependencies**: Task 3.1
- **Validation**: 既存テストパス

### Task 3.3: フェーズ別レート制限のテスト追加
- **Location**: `tests/test_api.py`, `tests/test_openai_llm.py`
- **Dependencies**: Task 3.1, 3.2
- **テストケース**:
  - Phase 0直後のPhase 0: sleepする
  - Phase 1直後のPhase 2: sleepしない
  - Phase 2直後のPhase 2: sleepする
- **Validation**: `python -m pytest tests/test_api.py tests/test_openai_llm.py -v`

## Sprint 4: UIポーリング適応化

**Goal**: ストリーミング中のUI更新を滑らかにし、アイドル時のCPU使用を抑える

**Demo/Validation**:
- `python -m pytest tests/ -q` 全テストパス
- ストリーミング表示が目視でスムーズになること

### Task 4.1: 適応型ポーリング間隔の実装
- **Location**: `realtime_translator/app.py` — `_poll_queue`
- **Description**: キューにアイテムがある場合は高速(10ms)、空の場合は低速(100ms)でポーリング
- **Dependencies**: なし
- **変更内容**:
  ```python
  _POLL_FAST_MS = 10
  _POLL_IDLE_MS = 100

  def _poll_queue(self) -> None:
      had_items = False
      try:
          while True:
              item = self._ui_queue.get_nowait()
              had_items = True
              ...
      except queue.Empty:
          pass
      interval = _POLL_FAST_MS if had_items else _POLL_IDLE_MS
      self.root.after(interval, self._poll_queue)
  ```
- **Validation**: 既存テストパス + 目視確認

## Testing Strategy
- `python -m pytest tests/ -q` — 各Sprint完了時に全テストパス
- Sprint 1: プロンプト英語化テスト + show_original 切替テスト + UI目視確認
- Sprint 2: SILENCE_SENTINEL テスト + Phase 1 非ストリーミングテスト
- Sprint 3: レート制限テスト（Phase間の待機時間検証）
- Sprint 4: UI目視確認（ストリーミング滑らかさ）

## Potential Risks & Gotchas
1. **英語プロンプトで翻訳品質が変わる可能性**（案A）
   - 対策: Gemini/OpenAIは多言語対応なので英語指示でも日本語出力は問題ないはず
   - ただし微妙なニュアンスの差が出る可能性あり。実運用で比較検証
   - 言語名は日本語のまま（"英語", "日本語"）なので翻訳方向の指示は明確
2. **LLMが指示を無視して「原文:」を出力する可能性**（案D）
   - `show_original=False` 時でもLLMが原文を出すケースあり
   - 最悪でもラベル重複するだけで機能的問題なし
3. **SILENCE_SENTINEL `…` の誤検出**（案E）
   - LLMが通常テキストとして `…` を出力する可能性
   - 代替: `<silent>` なら誤検出ゼロだがトークン削減効果なし
   - 判断: `…` が単独出力される確率は低い。問題が出たら `<silent>` に変更
4. **Phase 1 非ストリーミングでタイムアウトリスク**（案F）
   - ストリーミングでは部分結果が来るのでタイムアウト検出が容易
   - バッチではレスポンス全体を待つため、長い音声で遅延する可能性
   - 対策: タイムアウト設定を明示的に設定
5. **フェーズ別レート制限がGemini 15RPMを超える可能性**
   - listen + speak 合算リスクは既存と同等
6. **SILENCE_SENTINEL の検出への影響**
   - Phase 0 で無音指示は維持。`_on_partial_end` の削除ロジックは機能する
7. **`_STREAM_META` タプルサイズ変更の影響範囲**
   - `app.py` の `_on_partial_start` と `_on_transcript` のみ

## Rollback Plan
- 各Sprintは独立してロールバック可能
- Sprint 1: `prompts.py` + `constants.py` + `app.py` + `controller.py` を戻す
- Sprint 2: `constants.py` のSILENCE_SENTINEL + `api.py`/`openai_llm.py` のストリーミング復元
- Sprint 3: `api.py` + `openai_llm.py` の `_last_call_time` を単一変数に戻す
- Sprint 4: `app.py` のポーリング間隔を固定50msに戻す
