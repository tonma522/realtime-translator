以下を日本語でレビューしてください。

観点:
- 仕様の抜け
- バグや回帰リスク
- テスト不足
- 過剰設計
- 完了条件の不備
- 並列 TODO の依存関係と競合回避の妥当性
- TDD 手順との整合性

出力形式:
1. Findings を重要度順に列挙
2. 重大指摘がなければ「重大指摘なし」と明記
3. 最後に3行以内で総評

前回レビュー全文:
## レビュー結果

---

### Findings（重要度順）

---

#### 🔴 重大 1 — `resolve_virtual_stream_id` の KeyError リスク（バグ）

**該当箇所:** Task 1 Step 3、`stream_modes.py` 最小実装

```python
def resolve_virtual_stream_id(stream_id: str, mode: str) -> str:
    return f"{stream_id}_{normalize_translation_mode(mode, STREAM_MODE_DEFAULTS[stream_id])}"
```

`STREAM_MODE_DEFAULTS = {"listen": "en_ja", "speak": "ja_en"}` に存在しない `stream_id` が渡された場合（誤呼び出し・将来拡張）に `KeyError` が発生する。エラーハンドリングが一切ない。  
→ `STREAM_MODE_DEFAULTS.get(stream_id, "en_ja")` などのフォールバックか、事前バリデーション＋明示的例外が必要。テストケースも「無効な stream_id」のケースが抜けている。

---

#### 🔴 重大 2 — `direction_parse_failed` 時のフォールバック動作が未定義（仕様の抜け）

**該当箇所:** Notes for Execution、`auto_direction.py`

`auto` でLLMが `DIRECTION:` を正しく返さなかった場合に `direction_parse_failed` とすることは定義されているが、その後の動作が完全に未定義：

- LLMにリトライするか？
- フォールバック方向（例: `en_ja`）で処理継続するか？
- ユーザに通知するか（ UI でどう見えるか）？
- 音声チャンクは破棄するか保持するか？

UI 表示については「見えるが」とだけ書かれており、`tools_panel.py` でどのようなラベル・色・アイコンで表示するかが未定義。

---

#### 🔴 重大 3 — Task 4 の `api.py` / `openai_stt.py` / `whisper_stt.py` 変更内容が未定義（完了条件不備）

**該当箇所:** Task 4 Step 3〜4

他タスクには最小実装コードスニペットがあるが、Task 4 Step 4 の「worker 経路ごとの `resolved_direction` / `direction_source` 取り回しを実装する」に対応する最小実装例がない。`api.py` / `openai_stt.py` / `whisper_stt.py` の具体的な変更点（どのイベント・フィールドを追加するか）が本文中で一切記述されておらず、実装担当者が判断できない。TDD の「Step 3: 最小実装」が事実上空白になっている。

---

#### 🔴 重大 4 — `usable_for_downstream` が他エラー種別で誤動作するリスク（バグリスク）

**該当箇所:** Task 2 Step 3、`HistoryEntry.usable_for_downstream`

```python
@property
def usable_for_downstream(self) -> bool:
    return self.error != "direction_parse_failed"
```

将来 `error` に別の値（例: `"stt_timeout"`, `"llm_error"` など）が追加された場合、それらは `True`（下流利用可能）として扱われる。この挙動が意図的かどうかの仕様記述がない。テストも `error=None` / `error="direction_parse_failed"` の2ケースのみで、`error="other_error"` のケースが欠けている。

---

#### 🟡 中程度 1 — streaming 途中の `DIRECTION` 行が切れた場合の処理が未定義（仕様の抜け）

**該当箇所:** `auto_direction.py`、Task 3

`parse_direction_header` は完全な1行を想定しているが、LLM の streaming では `"DIREC"` → `"TION: en_ja\n"` のようにチャンク境界が行の途中に来る可能性がある。バッファリング戦略が未定義で、テストも CRLF のみカバーしている。`DIRECTION:  en_ja`（空白2個）や `direction: en_ja`（小文字）への耐性も未確認。

---

#### 🟡 中程度 2 — `virtual_stream_id` の文字列形式が曖昧（設計リスク）

**該当箇所:** Task 1 Step 3

`f"{stream_id}_{mode}"` の形式では `listen_en_ja` という値になる。`en_ja` 自体にアンダースコアが含まれているため、`"listen"` + `"en_ja"` なのか `"listen_en"` + `"ja"` なのかを文字列のみからパースできない。現在は `virtual_stream_id` をパースする処理はないが、将来追加された際に曖昧になる。セパレータを `":"` や `"|"` に変更するか、構造体で管理する方が安全。

---

#### 🟡 中程度 3 — Task 6 の速度テスト戦略が不明確（テスト不足・過剰設計）

**該当箇所:** Task 6 Step 1〜3

`test_auto_first_translation_latency_within_budget` の閾値 `p95_ms <= 200` / `ratio <= 1.2` の根拠が不明。「厳密な wall-clock 実測でなくてもよい」とあるが、CI 環境でのフレーキーリスクへの対処が未定義。また「4経路 × 8ケース = 32」という計算が、`listen`/`speak` × 3モード × 2入力言語 = 最大12ケースと整合しておらず、ケース定義が不明確。

---

#### 🟡 中程度 4 — `test_tools_panel.py` の具体的テスト内容が未定義（テスト不足）

**該当箇所:** File Structure、Parallel Todo Plan

`test_tools_panel.py` が正式スコープに追加されたとあるが、本文中で何をテストするか（`resolved_direction` 表示、`direction_parse_failed` 表示、再翻訳不可制御など）が一切記述されていない。Task 2 Step 1 のテストスニペットにも `tools_panel.py` 関連のテストが含まれていない。

---

#### 🟡 中程度 5 — `normalize_stt_language` のテストカバレッジ不足（テスト不足）

**該当箇所:** Task 3 Step 1、`auto_direction.py`

`en-AU` / `zh-CN` / `""` / `None` などのエッジケースのテストがない。`en-US` / `en-GB` / `ja-JP` / `unknown` のみカバーされているが、実際の外部STT（OpenAI Whisper API）はより多様な言語コードを返す可能性がある。

---

#### 🟡 中程度 6 — Task 3 Step 3 の最小実装が不完全（TDD整合性）

**該当箇所:** Task 3 Step 3

`build_prompt` 内で `is_auto_stream()` を呼んでいるが、このヘルパーの定義が最小実装スニペットに含まれていない。Step 3 のコードをそのまま実行すると `NameError` になり、TDD の「赤→緑」が達成できない。

---

#### 🟢 軽微 1 — `direction_source` の完全な列挙が未定義

`"stt_metadata"` という値が登場するが、その完全な列挙（`"llm_parse"`, `"fallback"` など）と各値が下流でどう扱われるかが未定義。将来の実装者が独自値を使い始めるリスクがある。

---

#### 🟢 軽微 2 — UI ラベルと内部コードのマッピングテストが抜けている

「英語→日本語」→`"en_ja"` の変換ロジックのテストがない。表示文字列と内部コードが乖離した場合（例：設定ファイルに「英語→日本語」が文字列で保存されてしまう）のバグを検出できない。

---

### 総評

`auto` モードの設計骨格（`virtual_stream_id` + `resolved_direction` + `direction_parse_failed` 除外）は筋が良く、TDD 手順と並列 TODO の依存順序も概ね妥当。ただし `direction_parse_failed` 時のフォールバック動作未定義、Task 4 の STT ワーカー変更内容の仕様欠落、`resolve_virtual_stream_id` の KeyError リスクの3点は実装開始前に必ず仕様を固める必要があり、このまま着手すると中断・手戻りが発生する可能性が高い。速度テストの閾値根拠と `usable_for_downstream` の将来拡張への耐性も、着手前に方針を決めておくことを推奨する。


前回の重大指摘要約:
# High Findings Summary
- [High] `resolve_virtual_stream_id` の unknown stream_id で KeyError になるリスク
- [High] `direction_parse_failed` 後のフォールバック動作と UI 扱いが未定義
- [High] Task 4 の `api.py` / `openai_stt.py` / `whisper_stt.py` の最小実装が空白で、イベント契約が不明
- [High] `usable_for_downstream` が `direction_parse_failed` 以外のエラーを通してしまう


今回すでに反映した修正:
- `resolve_virtual_stream_id` の unknown stream_id を明示的 ValueError に変更し、失敗テストを追加
- `direction_parse_failed` の既定動作を「推測フォールバックなし・自動リトライなし・不完全履歴保存・UIで方向未確定表示」に明記
- Task 4 に `ApiRequest` / `translation_done` / STT metadata 取り回しの最小実装スニペットを追加
- `usable_for_downstream` を `error is None` の安全側デフォルトに変更し、他エラー除外テストを追加
- `DirectionHeaderParser` と部分チャンクバッファの最小実装スニペットを追加

対象:
# Bidirectional Stream Modes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PC音声とマイクの両方で `英語→日本語` / `日本語→英語` / `同時翻訳` を独立設定でき、`auto` でも速度優先で方向確定と履歴互換を維持する。

**Architecture:** 既存の `listen` / `speak` を互換用 `stream_id` として残しつつ、翻訳方向は `virtual_stream_id` と `resolved_direction` に移す。方向決定は `stream_modes.py` と `auto_direction.py` に集約し、`controller.py` はソース別設定解決と worker 配線、`app.py` / `settings_window.py` は UI 表示と保存復元に専念させる。`auto` は wire format の全面共通化ではなく、`resolved_direction` / `original` / `translation_delta` / `translation_final` の内部イベントで共通化する。

**Tech Stack:** Python 3.11, tkinter, pytest, Gemini/OpenAI/OpenRouter STT+LLM workers

---

## File Structure

- Create: `realtime_translator/stream_modes.py`
  - 仮想ストリーム定義、`pc_audio_mode` / `mic_mode` 正規化、`stream_id` と `virtual_stream_id` の相互解決、旧履歴フォールバックを担当する
- Create: `realtime_translator/auto_direction.py`
  - `DIRECTION` ヘッダの streaming パース、STT 言語コード正規化、`resolved_direction` / `direction_source` 解決を担当する
- Create: `tests/test_stream_modes.py`
  - 仮想ストリーム定義、モード正規化、旧履歴フォールバックの単体テストを担当する
- Modify: `realtime_translator/constants.py`
  - 旧 `STREAM_LANGS` / `_STREAM_META` 依存を、新メタデータ参照へ寄せる
- Modify: `realtime_translator/prompts.py`
  - 固定方向 / auto / 再翻訳の prompt 契約を仮想ストリーム対応へ拡張する
- Modify: `realtime_translator/controller.py`
  - `StartConfig` 拡張、ソース別モード解決、worker 入力の `virtual_stream_id` 化、`resolved_direction` 履歴保存、`direction_parse_failed` 制御を担当する
- Modify: `realtime_translator/history.py`
  - `HistoryEntry` に `virtual_stream_id` / `resolved_direction` / `error` を追加し、下流フィルタ用ヘルパーを持たせる
- Modify: `realtime_translator/retranslation.py`
  - 再翻訳時に `resolved_direction` 優先で方向解決し、`direction_parse_failed` を除外する
- Modify: `realtime_translator/assist.py`
  - 返答アシスト / 議事録生成から不完全履歴を除外する
- Modify: `realtime_translator/tools_panel.py`
  - 履歴表示と再翻訳操作を `resolved_direction` / `error` 対応へ拡張する
- Modify: `realtime_translator/config.py`
  - `pc_audio_mode` / `mic_mode` の保存読込、`stream_modes.py` の正規化ヘルパー呼び出し、診断ログ追加を担当する
- Modify: `realtime_translator/app.py`
  - 新設定変数、ヘッダ表示、partial 表示の方向確定、履歴エラー表示、既定エクスポート除外を担当する
- Modify: `realtime_translator/settings_window.py`
  - `PC音声モード` / `マイクモード` コンボボックス、PTT 説明、外部STT時の制約表示を担当する
- Test: `tests/test_stream_modes.py`
- Test: `tests/test_prompts.py`
- Test: `tests/test_controller.py`
- Test: `tests/test_config.py`
- Test: `tests/test_history.py`
- Test: `tests/test_retranslation.py`
- Test: `tests/test_assist.py`
- Test: `tests/test_tools_panel.py`
- Test: `tests/test_integration.py`

### Task 1: 仮想ストリーム定義と設定正規化を追加する

**Files:**
- Create: `realtime_translator/stream_modes.py`
- Modify: `realtime_translator/constants.py`
- Test: `tests/test_stream_modes.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: 設定正規化と仮想ストリーム定義の失敗テストを書く**

```python
def test_normalize_translation_mode_invalid_value_falls_back_to_default():
    assert normalize_translation_mode("bad", "en_ja") == "en_ja"


def test_resolve_virtual_stream_id_for_pc_audio_auto():
    assert resolve_virtual_stream_id("listen", "auto") == "listen_auto"


def test_resolve_virtual_stream_id_rejects_unknown_stream():
    with pytest.raises(ValueError, match="unknown stream_id"):
        resolve_virtual_stream_id("other", "auto")
```

- [ ] **Step 2: 失敗を確認する**

Run: `pytest tests/test_stream_modes.py tests/test_prompts.py -k "translation_mode or virtual_stream_id or listen_auto" -v`
Expected: `ImportError` または `AttributeError` で `normalize_translation_mode` / `resolve_virtual_stream_id` が未定義

- [ ] **Step 3: 最小実装を入れる**

```python
STREAM_MODE_DEFAULTS = {"listen": "en_ja", "speak": "ja_en"}
VALID_TRANSLATION_MODES = {"en_ja", "ja_en", "auto"}


def normalize_translation_mode(value: object, default: str) -> str:
    return value if value in VALID_TRANSLATION_MODES else default


def resolve_virtual_stream_id(stream_id: str, mode: str) -> str:
    if stream_id not in STREAM_MODE_DEFAULTS:
        raise ValueError(f"unknown stream_id: {stream_id}")
    normalized_mode = normalize_translation_mode(mode, STREAM_MODE_DEFAULTS[stream_id])
    return f"{stream_id}_{normalized_mode}"


def is_auto_stream(stream_id: str) -> bool:
    return stream_id.endswith("_auto")
```

- [ ] **Step 4: 仮想ストリーム定義を `constants.py` と prompt 利用側へ組み込み、テストを再実行する**

Run: `pytest tests/test_stream_modes.py tests/test_prompts.py -v`
Expected: 新規 `virtual_stream_id` / `auto` ケースが PASS し、既存ケースも回帰しない

- [ ] **Step 5: コミットする**

```bash
git add realtime_translator/stream_modes.py realtime_translator/constants.py tests/test_stream_modes.py tests/test_prompts.py
git commit -m "feat: add virtual stream mode metadata"
```

### Task 2: 履歴契約と下流除外ルールを固定する

**Files:**
- Modify: `realtime_translator/history.py`
- Modify: `realtime_translator/retranslation.py`
- Modify: `realtime_translator/assist.py`
- Test: `tests/test_history.py`
- Test: `tests/test_retranslation.py`
- Test: `tests/test_assist.py`

- [ ] **Step 1: 新履歴フィールドと除外ルールの失敗テストを書く**

```python
def test_append_keeps_stream_id_and_virtual_stream_id():
    entry = history.append(
        stream_id="listen",
        timestamp="12:00:00",
        original="Hello",
        translation="こんにちは",
        virtual_stream_id="listen_auto",
        resolved_direction="en_ja",
    )
    assert entry.stream_id == "listen"
    assert entry.virtual_stream_id == "listen_auto"


def test_direction_parse_failed_entries_are_excluded_from_assist():
    history.append("listen", "12:00:00", "", "", virtual_stream_id="listen_auto", resolved_direction=None, error="direction_parse_failed")
    assert build_history_for_assist(history.all_entries()) == []


def test_other_error_entries_are_also_excluded_from_downstream():
    entry = history.append("listen", "12:00:00", "", "", error="stt_timeout")
    assert entry.usable_for_downstream is False
```

- [ ] **Step 2: 失敗を確認する**

Run: `pytest tests/test_history.py tests/test_retranslation.py tests/test_assist.py -k "virtual_stream_id or direction_parse_failed or resolved_direction" -v`
Expected: `TypeError` か assertion failure で新引数と除外ヘルパーが未対応

- [ ] **Step 3: 最小実装を入れる**

```python
@dataclass
class HistoryEntry:
    seq: int
    stream_id: str
    timestamp: str
    original: str
    translation: str
    virtual_stream_id: str | None = None
    resolved_direction: str | None = None
    error: str | None = None

    @property
    def usable_for_downstream(self) -> bool:
        return self.error is None
```

- [ ] **Step 4: 再翻訳とアシストの利用側を更新し、テストを再実行する**

Run: `pytest tests/test_history.py tests/test_retranslation.py tests/test_assist.py -v`
Expected: `direction_parse_failed` が再翻訳・返答アシスト・議事録の対象外になり、旧ケースも PASS

- [ ] **Step 5: コミットする**

```bash
git add realtime_translator/history.py realtime_translator/retranslation.py realtime_translator/assist.py tests/test_history.py tests/test_retranslation.py tests/test_assist.py
git commit -m "feat: extend history with virtual stream metadata"
```

### Task 3: prompt 契約と auto 方向解決ヘルパーを導入する

**Files:**
- Create: `realtime_translator/auto_direction.py`
- Modify: `realtime_translator/prompts.py`
- Test: `tests/test_prompts.py`
- Test: `tests/test_controller.py`

- [ ] **Step 1: auto prompt 契約と方向パーサの失敗テストを書く**

```python
def test_build_prompt_for_llm_auto_contains_direction_and_translation_contract():
    prompt = build_prompt("listen_auto", "会議", show_original=False)
    assert "DIRECTION:" in prompt
    assert "TRANSLATION:" in prompt


def test_parse_direction_header_accepts_crlf():
    event = parse_direction_header("DIRECTION: en_ja\r\n")
    assert event.resolved_direction == "en_ja"


def test_parse_direction_header_buffers_partial_chunks_until_newline():
    parser = DirectionHeaderParser()
    assert parser.feed("DIREC") is None
    event = parser.feed("TION: en_ja\r\n")
    assert event.resolved_direction == "en_ja"
```

- [ ] **Step 2: 失敗を確認する**

Run: `pytest tests/test_prompts.py tests/test_controller.py -k "listen_auto or parse_direction_header or resolved_direction" -v`
Expected: `KeyError`、`ImportError`、または期待文字列不一致で FAIL

- [ ] **Step 3: 最小実装を入れる**

```python
def build_prompt(stream_id: str, context: str, show_original: bool = True) -> str:
    if is_auto_stream(stream_id):
        return (
            "Decide whether the input is English or Japanese.\n"
            "Reply in this exact format:\n"
            "DIRECTION: en_ja | ja_en\n"
            "TRANSLATION: <translated text>"
        )
```

```python
def normalize_stt_language(code: str | None) -> str | None:
    if code in ("en", "en-US", "en-GB"):
        return "en"
    if code in ("ja", "ja-JP"):
        return "ja"
    return None


class DirectionHeaderParser:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str):
        self._buffer += chunk
        if "\n" not in self._buffer and "\r\n" not in self._buffer:
            return None
        ...
```

- [ ] **Step 4: prompt/パーサ系テストを再実行する**

Run: `pytest tests/test_prompts.py tests/test_controller.py -v`
Expected: 固定方向 prompt と auto prompt の両方が PASS し、旧 `listen` / `speak` ケースも維持される

- [ ] **Step 5: コミットする**

```bash
git add realtime_translator/auto_direction.py realtime_translator/prompts.py tests/test_prompts.py tests/test_controller.py
git commit -m "feat: add auto direction prompt contract"
```

### Task 4: controller に仮想ストリーム解決と auto イベント処理を組み込む

**Files:**
- Modify: `realtime_translator/controller.py`
- Modify: `realtime_translator/api.py`
- Modify: `realtime_translator/openai_stt.py`
- Modify: `realtime_translator/whisper_stt.py`
- Test: `tests/test_controller.py`
- Test: `tests/test_integration.py`

- [ ] **Step 1: StartConfig 拡張と経路別方向解決の失敗テストを書く**

```python
def test_on_audio_chunk_uses_listen_auto_virtual_stream_id():
    ctrl.start(_make_config(pc_audio_mode="auto"))
    ctrl.on_audio_chunk(b"wav", "listen")
    req = ctrl._api_worker_listen.submitted[0]
    assert req.stream_id == "listen_auto"


def test_openai_stt_metadata_en_resolves_to_en_ja():
    assert resolve_direction_from_stt_language("en-US") == ("en_ja", "stt_metadata")
```

- [ ] **Step 2: 失敗を確認する**

Run: `pytest tests/test_controller.py -k "pc_audio_mode or mic_mode or virtual_stream_id or stt_metadata" -v`
Expected: `TypeError` か assertion failure で `StartConfig` と routing が未対応

- [ ] **Step 3: 最小実装を入れる**

```python
@dataclass
class StartConfig:
    ...
    pc_audio_mode: str = "en_ja"
    mic_mode: str = "ja_en"
```

```python
def _resolve_stream_for_source(self, stream_id: str, config: StartConfig) -> str:
    mode = config.pc_audio_mode if stream_id == "listen" else config.mic_mode
    return resolve_virtual_stream_id(stream_id, mode)
```

```python
request = ApiRequest(
    wav_bytes=wav_bytes,
    prompt=prompt,
    stream_id=virtual_stream_id,
    phase=0,
    context=self._context,
    source_stream_id=stream_id,
    resolved_direction=None,
    direction_source=None,
)
```

```python
ui_queue.put((
    "translation_done",
    source_stream_id,
    virtual_stream_id,
    resolved_direction,
    timestamp,
    original,
    translation,
    error,
))
```

```python
normalized_lang = normalize_stt_language(stt_language)
if is_auto_stream(virtual_stream_id) and normalized_lang is not None:
    resolved_direction = "en_ja" if normalized_lang == "en" else "ja_en"
    direction_source = "stt_metadata"
else:
    resolved_direction = None
    direction_source = None
```

- [ ] **Step 4: worker 経路ごとの `resolved_direction` / `direction_source` 取り回しを実装し、対象テストを再実行する**

Run: `pytest tests/test_controller.py tests/test_integration.py -v`
Expected: 通常・2フェーズ・Whisper・外部STT の経路で `virtual_stream_id` と `resolved_direction` が期待どおりになる

- [ ] **Step 5: コミットする**

```bash
git add realtime_translator/controller.py realtime_translator/api.py realtime_translator/openai_stt.py realtime_translator/whisper_stt.py tests/test_controller.py tests/test_integration.py
git commit -m "feat: route controller through virtual stream ids"
```

### Task 5: UI と設定画面を仮想ストリーム対応へ更新する

**Files:**
- Modify: `realtime_translator/app.py`
- Modify: `realtime_translator/settings_window.py`
- Modify: `realtime_translator/config.py`
- Test: `tests/test_config.py`
- Test: `tests/test_integration.py`

- [ ] **Step 1: UI 表示と保存復元の失敗テストを書く**

```python
def test_load_config_restores_pc_audio_mode_and_mic_mode():
    save_config({"pc_audio_mode": "auto", "mic_mode": "en_ja"})
    loaded = load_config()
    assert loaded["pc_audio_mode"] == "auto"
    assert loaded["mic_mode"] == "en_ja"


def test_partial_header_uses_pending_auto_label_until_direction_resolves():
    assert format_stream_header("listen", "listen_auto", None) == "PC音声 同時翻訳"
```

- [ ] **Step 2: 失敗を確認する**

Run: `pytest tests/test_config.py tests/test_integration.py -k "pc_audio_mode or mic_mode or auto_label or settings" -v`
Expected: 設定キー未保存、ヘッダ整形未実装、または UI ラベル不一致で FAIL

- [ ] **Step 3: 最小実装を入れる**

```python
config = {
    ...
    "pc_audio_mode": normalize_translation_mode(self._pc_audio_mode_var.get(), "en_ja"),
    "mic_mode": normalize_translation_mode(self._mic_mode_var.get(), "ja_en"),
}
```

```python
ttk.Combobox(frame, textvariable=self._app._pc_audio_mode_var, values=["英語→日本語", "日本語→英語", "同時翻訳"], state="readonly")
```

- [ ] **Step 4: UI 関連テストを再実行する**

Run: `pytest tests/test_config.py tests/test_integration.py -v`
Expected: 設定の保存復元、`同時翻訳` 表示、`direction_parse_failed` の見分け方、既定エクスポート除外が PASS

- [ ] **Step 5: コミットする**

```bash
git add realtime_translator/app.py realtime_translator/settings_window.py realtime_translator/config.py tests/test_config.py tests/test_integration.py
git commit -m "feat: add source-specific translation mode controls"
```

### Task 6: 統合回帰と速度計測の受け入れラインを固める

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `tests/test_controller.py`
- Modify: `tests/test_prompts.py`
- Modify: `README.md`

- [ ] **Step 1: 32ケース最小行列と速度閾値の失敗テストを書く**

```python
@pytest.mark.parametrize("route_case", [
    "listen_en_ja",
    "listen_ja_en",
    "speak_en_ja",
    "speak_ja_en",
    "listen_auto_en_input",
    "listen_auto_ja_input",
    "speak_auto_en_input",
    "speak_auto_ja_input",
])
def test_route_case_resolves_expected_direction(route_case):
    ...


def test_auto_first_translation_latency_within_budget():
    assert auto_latency_p95_ms <= 200
    assert auto_latency_ratio <= 1.2
```

- [ ] **Step 2: 失敗を確認する**

Run: `pytest tests/test_integration.py tests/test_controller.py -k "auto_latency or route_case" -v`
Expected: ケース不足または測定ヘルパー未実装で FAIL

- [ ] **Step 3: 最小実装を入れる**

```python
ROUTE_CASES = [
    ("normal", "listen", "en_ja"),
    ("normal", "listen", "ja_en"),
    ("normal", "speak", "en_ja"),
    ("normal", "speak", "ja_en"),
    ...
]
```

- [ ] **Step 4: 対象スイートと主要回帰スイートを実行する**

Run: `pytest tests/test_prompts.py tests/test_config.py tests/test_history.py tests/test_retranslation.py tests/test_controller.py tests/test_assist.py tests/test_integration.py -v`
Expected: 全 PASS。少なくとも `auto` で `DIRECTION` 後に訳文表示、`direction_parse_failed` 除外、外部STT 分岐、32ケース最小集合が担保される

- [ ] **Step 5: README を更新してコミットする**

```bash
git add tests/test_integration.py tests/test_controller.py tests/test_prompts.py README.md
git commit -m "test: add bidirectional stream mode coverage"
```

## Notes for Execution

- 実装は必ず TDD で進める。各タスクの Step 1 と Step 2 を飛ばさない
- `stream_id` は互換用に残す。新値を `stream_id` に保存しない
- `direction_parse_failed` は UI では見えるが、再翻訳・返答アシスト・議事録・既定エクスポートには混ぜない
- `direction_parse_failed` 時に固定方向への推測フォールバックはしない。LLM 自動リトライも既定では行わず、そのチャンクは不完全履歴として保持して終了する
- `direction_parse_failed` 時は、取得済み `original` があれば保持し、`translation=""`、`resolved_direction=None`、`error="direction_parse_failed"` を保存する
- `direction_parse_failed` の UI は少なくとも「方向未確定」ラベル、エラー色、再翻訳無効、ToolsPanel での理由表示を持たせる
- 外部STT の言語コード正規化は `en-US` / `en-GB` / `ja-JP` / `unknown` を最低限カバーする
- `auto` の速度検証は厳密な wall-clock 実測でなくてもよいが、固定方向比較の同一テスト harness で相対評価できる形にする

## Parallel Todo Plan

### サブエージェント構成

- `gpt-5.4-mini / core-foundation`
  - 所有: `realtime_translator/stream_modes.py`, `realtime_translator/auto_direction.py`, `realtime_translator/constants.py`, `realtime_translator/prompts.py`
  - 役割: 仮想ストリーム定義、モード正規化、`resolved_direction` 解決、`auto` の `DIRECTION` / `TRANSLATION` 契約固定
- `gpt-5.4-mini / controller-stt`
  - 所有: `realtime_translator/controller.py`, `realtime_translator/api.py`, `realtime_translator/openai_stt.py`, `realtime_translator/whisper_stt.py`
  - 役割: `StartConfig.pc_audio_mode` / `mic_mode` 追加、仮想ストリーム解決、STT言語優先、LLMフォールバック、UIキュー payload 更新
- `gpt-5.4-mini / history-downstream`
  - 所有: `realtime_translator/history.py`, `realtime_translator/retranslation.py`, `realtime_translator/assist.py`, `realtime_translator/tools_panel.py`
  - 役割: 履歴契約拡張、`direction_parse_failed` 除外、再翻訳不可制御、下流表示更新
- `gpt-5.4-mini / ui-config`
  - 所有: `realtime_translator/app.py`, `realtime_translator/settings_window.py`, `realtime_translator/config.py`
  - 役割: `PC音声モード` / `マイクモード` UI、保存復元、partial 表示切替、エラー表示、既定エクスポート除外
- `gpt-5.4-mini / tests`
  - 所有: `tests/test_stream_modes.py`, `tests/test_prompts.py`, `tests/test_config.py`, `tests/test_history.py`, `tests/test_retranslation.py`, `tests/test_assist.py`, `tests/test_controller.py`, `tests/test_tools_panel.py`, `tests/test_integration.py`
  - 役割: 各実装レーンの直前に失敗テストを置く支援と、最後の 4経路 × 8ケース統合検証

### 実行順 TODO

- [ ] `tests` と `core-foundation` で `test_stream_modes.py` / `test_prompts.py` の失敗テストを先に置く
- [ ] `core-foundation` で `stream_modes.py` と `constants.py` を実装する
- [ ] `tests` と `ui-config` で `test_config.py` の失敗テストを先に置く
- [ ] `ui-config` で `config.py` の `pc_audio_mode` / `mic_mode` 保存・正規化呼び出しを実装する
- [ ] `tests` と `history-downstream` で `test_history.py` / `test_retranslation.py` / `test_assist.py` / `test_tools_panel.py` の失敗テストを先に置く
- [ ] `history-downstream` で `history.py` の `virtual_stream_id` / `resolved_direction` / `error` 拡張を実装する
- [ ] `core-foundation` で `auto_direction.py` と `prompts.py` の `auto` 契約を実装する
- [ ] `tests` と `controller-stt` で `test_controller.py` の失敗テストを先に置く
- [ ] `controller-stt` で `StartConfig` 拡張と仮想ストリーム解決を実装する
- [ ] `controller-stt` で `api.py` / `openai_stt.py` / `whisper_stt.py` のイベント契約を更新する
- [ ] `history-downstream` で `retranslation.py` / `assist.py` / `tools_panel.py` を `direction_parse_failed` 対応にする
- [ ] `tests` と `ui-config` で `test_integration.py` に入る前の UI 失敗テストを先に置く
- [ ] `ui-config` で `app.py` / `settings_window.py` のモードUIと結果表示切替を実装する
- [ ] `tests` で最後に `test_integration.py` の 4経路 × 8ケースを固める

### 競合回避ルール

- [ ] `constants.py` は `core-foundation` だけが編集する
- [ ] `config.py` は `ui-config` だけが編集する
- [ ] `stream_modes.py` の `normalize_translation_mode()` を唯一の正規化関数とし、`config.py` はそれを呼ぶだけにする
- [ ] `controller.py` の UIキュー payload を確定するまで `app.py` のイベント処理を触らない
- [ ] `history.py` の `append()` シグネチャ確定前に `retranslation.py` / `assist.py` / `tools_panel.py` を変更しない
- [ ] `tests/test_controller.py` と `tests/test_integration.py` は同時に編集しない
- [ ] 各サブエージェントは自分の所有ファイル以外を編集しない

