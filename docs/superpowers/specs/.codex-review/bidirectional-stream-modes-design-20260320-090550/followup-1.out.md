Section: 表示設計 > 実行中表示  
Replace:  
`auto` では `DIRECTION` 行の後に `ORIGINAL` を受け取り、その後で `TRANSLATION` を streaming 表示する前提。  
With:  
`auto` では「方向確定」と「訳文 streaming 開始」を最短化する。UI の開始条件は `resolved_direction` の確定のみとし、`original` の受信完了は待たない。`show_original=true` の場合も、原文は訳文とは独立に後着で反映してよい。`show_original=false` の場合は原文を UI に流さない。原文取得のために訳文開始を遅らせる設計は採らない。  
Reason: `ORIGINAL` 前置を必須にすると、原文長に比例して訳文開始が遅れ、速度優先要件と矛盾するため。

Section: 制御フロー設計 > 2フェーズ / Whisper / 外部STT  
Replace:  
`auto` の方向決定と streaming パースを、通常モードと同一の `DIRECTION` / `ORIGINAL` / `TRANSLATION` パーサで扱う前提。  
With:  
`auto` の共通化対象はテキスト wire format ではなく内部イベントとする。全経路は最終的に `resolved_direction`、`original`、`translation_delta`、`translation_final` を同じ内部インターフェースへ流す。  
- 通常モードの `auto` では、LLM 応答先頭ヘッダが `resolved_direction` の生成主体になる。  
- 2フェーズ / Whisper / 外部STT で STT が言語情報を返す場合は、STT 側が `resolved_direction` の生成主体であり、翻訳段は固定方向翻訳のみを行う。  
- STT が言語情報を返さない場合だけ、翻訳開始前に LLM で方向判定し、その後は固定方向翻訳に入る。  
必要に応じて `direction_source` (`llm_header` / `stt_metadata` / `llm_fallback`) を内部保持する。  
Reason: `DIRECTION` の生成主体を明確化し、STT 経路で不要な共通パーサ依存を避けるため。

Section: プロンプト設計 > 自動方向  
Replace:  
通常モードと2フェーズ後段のどちらでも `DIRECTION` / `ORIGINAL` / `TRANSLATION` の3行を必須とし、`ORIGINAL` を1行正規化して同一パーサで処理する記述。  
With:  
`auto` の応答契約は経路別に分ける。  
1. LLM 直結 `auto`  
```text
DIRECTION: en_ja | ja_en
TRANSLATION: <translated text>
```  
`TRANSLATION:` の本文は直後から streaming する。`ORIGINAL` は streaming 開始条件にしない。原文が必要な場合だけ、完了後メタデータとして別取得または末尾フィールドで返す。  
2. STT 確定 `auto`  
翻訳 LLM には固定方向プロンプトを渡し、出力は既存の固定方向フォーマットまたは訳文 streaming のみとする。この経路では `DIRECTION` 行を要求しない。  
パーサも経路別に分け、`direction_parse_failed` は LLM 直結 `auto` にだけ適用する。  
Reason: `ORIGINAL` 前置による遅延をなくしつつ、STT 経路の責務分離を明確にできるため。

Section: テスト方針 / 完了条件  
Replace:  
`auto` 用応答フォーマットに常に `DIRECTION` / `ORIGINAL` / `TRANSLATION` が含まれること、外部STT 経路でも `DIRECTION` パース前提で完了条件を置く記述。  
With:  
`auto` の検証は経路別に分ける。  
- LLM 直結 `auto`: 先頭 `DIRECTION` 確定後、原文待ちなしで `TRANSLATION` streaming が始まること。  
- STT 確定 `auto`: STT の言語情報または事前判定で `resolved_direction` が確定し、翻訳段で `DIRECTION` パースを要求しないこと。  
- `direction_parse_failed` は LLM 直結 `auto` のみで発生し、STT 確定経路では対象外であること。  
完了条件の「方向情報が構造化フォーマットから確実に抽出される」は、「方向情報が LLM ヘッダまたは STT メタデータから確定される」に置き換える。  
Reason: 旧文のままだと、実装者が全経路へ同一パーサを強制し、責務の曖昧さが残るため。