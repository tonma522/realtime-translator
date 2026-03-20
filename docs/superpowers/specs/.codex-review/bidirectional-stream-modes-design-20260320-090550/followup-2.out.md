Section: 表示設計 > 履歴  
Replace:  
`virtual_stream_id` / `resolved_direction` の保持と、旧形式 `listen` / `speak` フォールバックだけを述べている段落。  
With:  
新旧互換を含む履歴契約は以下で固定する。新規履歴エントリでは `stream_id`、`virtual_stream_id`、`resolved_direction`、`original`、`translation` を保持する。`stream_id` は互換維持用の入力ソース識別子として今後も保持し、値は常に `listen` または `speak` とする。UI色分け、入力ソース表示、旧呼び出し側互換には `stream_id` を使ってよい。一方、翻訳方向の解決には `stream_id` を使わず、`resolved_direction` を最優先し、次に `virtual_stream_id` を使う。`virtual_stream_id` は `listen_en_ja` / `listen_ja_en` / `listen_auto` / `speak_en_ja` / `speak_ja_en` / `speak_auto` のいずれかを取り、`resolved_direction` は最終確定した `en_ja` または `ja_en` を保持する。`auto` で方向確定に失敗した場合のみ `resolved_direction=null` を許容する。旧形式データで `virtual_stream_id` と `resolved_direction` が欠損している場合に限り、`listen` -> `listen_en_ja`、`speak` -> `speak_ja_en` のフォールバックを適用する。  
Reason: `stream_id` を残す範囲と、新フィールドへの責務移管範囲を明文化できます。

Section: エラーハンドリング  
Replace:  
`direction_parse_failed` 時に不完全履歴を残し、再翻訳無効・UIで区別表示するとだけ書いている段落。  
With:  
`auto` の構造化応答で `DIRECTION` が欠損または不正値だった場合は、推測補完せずエラーとして扱う。このとき履歴には `stream_id` と `virtual_stream_id` を保持したまま、`resolved_direction=null`、`translation=\"\"`、`error=\"direction_parse_failed\"` を持つ不完全履歴を保存する。`original` は取得済みの場合のみ保持する。不完全履歴は参照専用とし、再翻訳対象にしない。また、`返答アシスト` の入力履歴、`議事録生成` の本文候補、既定のエクスポート本文には混入させない。生履歴をそのまま出力する診断用途のエクスポートでのみ、`error` 付きレコードとして含めてよい。履歴一覧では通常エントリと視覚的に区別し、方向未確定であることが分かる表示にする。  
Reason: 不完全履歴の混入可否を下流機能ごとに固定でき、運用時の解釈ぶれを防げます。

Section: テスト方針 > controller / 統合テスト  
Replace:  
`direction_parse_failed` は再翻訳対象から除外、旧形式履歴はフォールバックで補完、という粒度のテスト項目。  
With:  
以下を追加で検証対象にする。`direction_parse_failed` な不完全履歴が再翻訳、返答アシスト、議事録生成、既定エクスポート本文から除外されること。新規履歴で `stream_id=listen/speak` が維持されつつ、方向解決は `resolved_direction` と `virtual_stream_id` を優先すること。旧形式履歴では `stream_id` のみから従来フォールバックできること。  
Reason: 互換契約と不完全履歴の扱いを、仕様だけでなく受け入れ条件として固定できます。