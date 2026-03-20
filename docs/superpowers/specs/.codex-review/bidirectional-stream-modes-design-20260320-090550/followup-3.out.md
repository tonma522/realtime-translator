Section: 完了条件  
Replace:  
`auto` モードの速度条件が「速度劣化を最小化」の定性的表現に留まり、経路カバレッジも「6つの仮想ストリームが動作する」だけで判定基準が弱い箇所。  
With:  
- `auto` モードでは、`DIRECTION` 行のパース完了から最初の `TRANSLATION` 文字列を UI に表示するまでの追加遅延が、同一入力・同一バックエンドの固定方向 streaming 経路比で `p95 200ms以下` かつ `1.2倍以内` であることを計測テストで確認できること。  
- 対象4経路（通常モード、2フェーズ、Whisper、外部STT）それぞれで、少なくとも `listen_en_ja`、`listen_ja_en`、`speak_en_ja`、`speak_ja_en`、`listen_auto(英語入力)`、`listen_auto(日本語入力)`、`speak_auto(英語入力)`、`speak_auto(日本語入力)` の8ケースが成功すること。  
- 上記全ケースで `virtual_stream_id`、`resolved_direction`、UI表示ラベル、履歴保存値が期待値と一致すること。  
- 外部STT経路では、`auto` の少なくとも各1ケースで「言語情報ありで即時確定」と「言語情報なしでLLM判定へフォールバック」の両分岐が検証されること。  
Reason: 速度優先を達成判定可能な数値に落とし込み、`auto` の両方向解決まで含めた最小カバレッジを完了条件として明示できるため。  

Section: 統合テスト  
Replace:  
「通常モード、2フェーズ、Whisper、外部STT の主要4経路について、少なくとも1本ずつ end-to-end に近い統合テストを持つこと」という記述。  
With:  
- 統合テストはパラメタライズドでよいが、対象4経路 × 8ケースの最低32ケースを持つこと。  
- 8ケースは `listen_en_ja`、`listen_ja_en`、`speak_en_ja`、`speak_ja_en`、`listen_auto(英語入力)`、`listen_auto(日本語入力)`、`speak_auto(英語入力)`、`speak_auto(日本語入力)` を最小集合とする。  
- 各ケースで、入力ソース、選択モード、実行経路から解決された `virtual_stream_id` と `resolved_direction`、UIラベル、履歴保存値までを end-to-end に検証する。  
- `auto` streaming ケースでは、`DIRECTION` 確定後に `ORIGINAL` が UI に出ないこと、かつ最初の `TRANSLATION` 表示が完了条件で定義した速度閾値内であることを検証する。  
- 外部STT経路では、`en` / `ja` の言語情報返却ケースに加え、言語情報なしでLLM判定へフォールバックするケースを含めること。  
Reason: 「経路ごとに1本」では6仮想ストリームすら証明できず、`auto` の両方向と外部STT分岐も取りこぼすため、最小テスト行列を明文化する必要がある。