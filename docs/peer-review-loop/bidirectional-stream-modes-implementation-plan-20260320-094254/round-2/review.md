## レビュー結果

---

### Findings（重要度順）

---

#### 🔴 重大 1 — `DirectionHeaderParser.feed()` の実装本体が `...` のまま（TDD不整合・バグリスク）

**該当箇所:** Task 3 Step 3

```python
def feed(self, chunk: str):
    self._buffer += chunk
    if "\n" not in self._buffer and "\r\n" not in self._buffer:
        return None
    ...  # ← ここが空白
```

改行検出後の処理が一切未定義のまま：

- `DIRECTION: en_ja\r\n` から `"en_ja"` を抽出するロジック
- 残りバッファ（`TRANSLATION:` 以降）の取り扱い
- 返却オブジェクトの型（`DirectionEvent` など）の定義がなく、テストが `event.resolved_direction` を参照できない

STT言語コードが不明な場合の `auto` メインパスはこの parser に依存するため、Step 3 で「赤→緑」が達成できない。前回指摘した Task 4 の空白と同質の問題が Task 3 に残存している。

---

#### 🟡 中程度 1 — `format_stream_header` の定義先と実装スニペットが未記載（完了条件不備）

**該当箇所:** Task 5 Step 1

```python
def test_partial_header_uses_pending_auto_label_until_direction_resolves():
    assert format_stream_header("listen", "listen_auto", None) == "PC音声 同時翻訳"
```

この関数がどのモジュール（`app.py`? `stream_modes.py`?）に属するか未定義。Task 5 Step 3 の最小実装スニペットにも記載がなく、`ui-config` サブエージェントが判断できない。

---

#### 🟡 中程度 2 — `build_history_for_assist` の定義先が未記載（完了条件不備）

**該当箇所:** Task 2 Step 1

```python
assert build_history_for_assist(history.all_entries()) == []
```

`assist.py` に属するのか `history.py` のヘルパーなのかが不明。Task 2 Step 3 の最小実装スニペットに含まれていないため、`history-downstream` サブエージェントが判断できない。

---

#### 🟡 中程度 3 — `build_prompt` の固定方向 branch がスニペットに欠落（TDD整合性）

**該当箇所:** Task 3 Step 3

最小実装スニペットは `is_auto_stream()` の branch のみを示し、固定方向（`listen_en_ja` 等）の場合の `build_prompt` 返却値が未定義。Task 3 Step 2 では旧 `listen` / `speak` ケースも維持することを期待しているが、Step 3 のコードではその部分が欠落しており TDD の「赤→緑」が固定方向ケースで達成できない。

---

#### 🟡 中程度 4 — `normalize_stt_language` エッジケーステストが未追加（継続・テスト不足）

**該当箇所:** Task 3 Step 1

前回指摘から変化なし。`en-AU`, `zh-CN`, `""`, `None` のカバレッジがない。OpenAI Whisper API は `en`, `en-US`, `en-GB`, `en-AU` など地域コード付きで返すことがあり、現状の正規化では `None` が返り `stt_metadata` パスに入れない。

---

#### 🟡 中程度 5 — `test_tools_panel.py` の具体的テスト内容が依然未定義（継続・テスト不足）

**該当箇所:** File Structure、Parallel Todo Plan

前回指摘から変化なし。`history-downstream` サブエージェントが `tools_panel.py` を所有しているが、何をテストすべきかの記述が本文中に一切ない。

---

#### 🟢 軽微 1 — `direction_source` の完全な値リストが未定義（継続）

`"stt_metadata"` は登場するが `"llm_parse"` / `"fallback"` 相当の値が未列挙。前回指摘から変化なし。

---

#### 🟢 軽微 2 — Task 6 の「32ケース」計算根拠が依然不明確（継続）

`@pytest.mark.parametrize` に8ケース、`ROUTE_CASES` は展開途中で `...`。「4経路 × 8ケース = 32」の分割が何を指すか未明。

---

#### 🟢 軽微 3 — `virtual_stream_id` セパレータ曖昧さ・速度テスト閾値根拠・UIラベルマッピングテスト（継続）

前回の軽微 2〜4 相当。いずれも未対応だが実装ブロッカーではない。

---

### 総評

前回の4件の重大指摘はいずれも実質的に対応されており、計画の品質は大きく向上した。今回の最大の残課題は `DirectionHeaderParser.feed()` の本体実装スニペットが `...` のままである点で、これは `auto` モードの LLM ストリーミング経路全体の TDD 実施を妨げる。加えて `format_stream_header` と `build_history_for_assist` の定義先が曖昧で、並列サブエージェントが実装着手時に判断を迫られる。これら3点を明記すれば着手可能な状態になる。
