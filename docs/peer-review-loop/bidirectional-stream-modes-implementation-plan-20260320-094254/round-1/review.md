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
