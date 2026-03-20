## レビュー結果

---

### Findings（重要度順）

---

#### 🟡 中程度 1 — `build_prompt` の固定方向 branch がスニペットに依然欠落（TDD整合性・継続）

**該当箇所:** Task 3 Step 3

```python
def build_prompt(stream_id: str, context: str, show_original: bool = True) -> str:
    if is_auto_stream(stream_id):
        return (...)
    # ← 固定方向 (listen_en_ja / speak_ja_en 等) の branch がない
```

Task 3 Step 2 では「旧 `listen` / `speak` ケースも維持」を期待しているが、Step 3 の最小実装スニペットには固定方向の branch が記載されていない。`test_prompts.py` で既存の固定方向ケースを書いても Step 3 時点で緑にならない。前回指摘（🟡中程度3）から変化なし。

---

#### 🟡 中程度 2 — Task 6 の `ROUTE_CASES` と「32ケース」の対応が依然不明確（継続）

**該当箇所:** Task 6 Step 3

`@pytest.mark.parametrize` の8ケースと `ROUTE_CASES` の `...` が接続されておらず、「4経路 × 8ケース = 32」の計算根拠が示されていない。Step 4 の合格基準に「32ケース最小集合が担保される」とあるが、Step 3 の実装スニペットでは8行しかない。サブエージェント `tests` がここで判断を迫られる。

---

#### 🟡 中程度 3 — `DirectionHeaderParser` が invalid header で `ValueError` を上げるが、呼び出し側の例外処理方針が未定義

**該当箇所:** Task 3 Step 3、Notes for Execution

スニペット実装は以下を行う：

```python
else:
    raise ValueError(f"invalid direction header: {header}")
```

しかし `controller.py` 側でこの例外をどう捕捉して `error="direction_parse_failed"` に変換するかが Task 4 Step 3 のスニペットに記載されていない。Notes で「LLM自動リトライも既定では行わない」とされているが、`ValueError` を catch しないと未処理例外でワーカースレッドが死ぬリスクがある。

---

#### 🟡 中程度 4 — `format_stream_header` で `source_stream_id == "listen"` 以外の場合（`"speak"` 固定方向など）のラベルが未テスト

**該当箇所:** Task 5 Step 3

実装スニペットは `"listen"` / それ以外の2分岐だが、テストケースは `"listen"` + `resolved_direction=None` の1ケースのみ。`"speak"` + `resolved_direction="ja_en"` や `resolved_direction="en_ja"` のラベルが仕様どおりか検証できない。Task 5 Step 1 の失敗テストに最低1ケース追加が必要。

---

#### 🟢 軽微 1 — `virtual_stream_id` セパレータ `_` が `stream_id` 内の `_` と衝突する可能性（継続）

`listen_auto` の `_` が `stream_id="listen"` + `mode="auto"` の結合として一意だが、将来 `stream_id` に `_` を含む値が増えた場合の曖昧さ解消ルールが未記載。現状は `STREAM_MODE_DEFAULTS` で許容 `stream_id` を列挙しているため実害なし。

---

#### 🟢 軽微 2 — `normalize_stt_language` で `zh-CN` を `None` 返すことの仕様的意図が未コメント

**該当箇所:** Task 3 Step 3

`zh-CN → None` は「中国語は方向推定対象外」という意図と読めるが、スニペットにコメントがなく、将来の実装者が誤解するリスクがある。軽微だが `auto_direction.py` のドキュメントに一行追記が望ましい。

---

#### 🟢 軽微 3 — 速度閾値 `200ms` / `1.2倍` の根拠が依然未記載（継続）

Task 6 Step 1 の `auto_latency_p95_ms <= 200` / `auto_latency_ratio <= 1.2` の値が何に基づくかが記載なし。テストを書く `tests` サブエージェントが数値を自由に変えてしまう可能性がある。

---

### 重大指摘なし

前回の🔴重大指摘（`DirectionHeaderParser.feed()` 本体未実装）は今回適切に解消されている。`build_history_for_assist()` / `format_stream_header()` の定義先も明記された。`test_tools_panel.py` の3ケース固定も追記済み。

---

### 総評

前回の重大指摘はすべて解消され、計画は着手可能な水準に達した。残課題は `build_prompt` 固定方向 branch の欠落と `ValueError` の呼び出し側捕捉方針の明記が最優先で、これらは `controller-stt` と `core-foundation` の並列実装の境界に跨がるため、競合回避ルールと合わせて補足しておくと安全。他の中程度指摘は実装ブロッカーにはならないが、並列サブエージェントが判断を迫られる箇所として残っている。
