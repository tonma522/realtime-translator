対象ファイル: docs/superpowers/specs/2026-03-20-bidirectional-stream-modes-design.md
日本語で簡潔に回答してください。

論点:
`auto` の共通プロトコルが速度優先要件と矛盾している。`DIRECTION` の後に `ORIGINAL` を全文1行で出してから `TRANSLATION` に入る仕様だと、訳文 streaming 開始が原文長ぶん遅れる。また外部STT/Whisper 経路では STT 側で方向確定すると書きつつ、同一パーサを使う前提もあり、`DIRECTION` の生成主体が曖昧。

求める出力形式:
Section: ...
Replace:
<旧文または置換対象要約>
With:
<新しい文案>
Reason: ...
