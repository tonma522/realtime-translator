以下を日本語でレビューしてください。

観点:
- 仕様の抜け
- バグや回帰リスク
- テスト不足
- 過剰設計
- 完了条件の不備

出力形式:
1. Findings を重要度順に列挙
2. 重大指摘がなければ「重大指摘なし」と明記
3. 最後に3行以内で総評
4. 現在の対象本文を正本として判断し、過去レビューの古い指摘はそのまま繰り返さない

前回レビュー全文:
サイズ都合で要約します。前回の主要指摘は次でした。
- [高] 後処理の統合ポイントが曖昧で、通常翻訳・2フェーズ翻訳・再翻訳の呼び出し位置が不明
- [高] 完了条件がなく、何をもって実装完了とするか不明
- [高] 英語出力で裸数字の読み付与範囲が広すぎ、型番・日付・バージョン番号との境界が不明
- [中] `Ra` 補足の有益性と他粗さ指標の扱いが曖昧
- [中] `JIS # / FEPA P / US Mesh / micron` の離散テーブルが仕様化されていない
- [中] LLM 非決定性を踏まえた回帰確認方法が曖昧

前回の重大指摘要約:
- [High] 後処理の統合ポイントをファイル名・関数名レベルで固定する必要がある
- [High] Done Criteria を追加する必要がある
- [High] 英語裸数字読みの除外対象を明記する必要がある

今回すでに反映した修正:
- `realtime_translator/translation_postprocess.py` と `realtime_translator/unit_tables.py` を spec 上の追加対象に固定
- `TranslatorApp._poll_queue()` の `translation_done` 分岐、および `RetranslationWorker._execute_retranslation()` を後処理の統合ポイントとして明記
- `ApiWorker._call_api()` と `OpenAiLlmWorker._handle_phase0_2()` は raw translation の emit のみに限定すると明記
- `Done Criteria` を追加し、適用経路と test suite 通過を完了条件に固定
- 英語数字読みの除外対象と判定原則を追加
- 離散変換テーブルの保存場所と `mesh` / `JIS #` / `FEPA P` の扱いを追加
- `Ra` は v1 で `um` 補足のみ、他粗さ指標は対象外と明記
- 回帰確認方法を deterministic test 中心で追加

注意:
- 現在の対象本文が正本です
- 過去レビューにある古い断片より、現在の対象本文を優先してください
- 高重要度の指摘が1件でも残るなら「重大指摘なし」とは書かないでください

対象:

```markdown
# Spoken Translation and Unit Annotation Design

Date: 2026-03-20
Status: Draft approved by user for implementation planning

## Goal

リアルタイム翻訳の既定動作を、読み上げやすい話し言葉優先の通訳文へ寄せる。
同時に、数値・単位・表面粗さ・砥粒サイズに関する補足情報を自動付与し、日英どちらでも実務会話中の誤解を減らす。

## Scope

本仕様は次を対象とする。

- 通常翻訳プロンプト
- 2フェーズ翻訳プロンプト
- 再翻訳プロンプト
- 翻訳結果に対する後処理
- 数値・単位補足の表示ルール
- 決定的後処理の統合ポイント
- 粒度変換テーブルと数値読み規則

本仕様は次を対象外とする。

- 音声認識そのものの改善
- 新しい外部 LLM の追加
- UI レイアウト変更

## Product Decision

採用方針は `LLM翻訳 + 決定的な後処理` の 2 段構成とする。

1. 翻訳段
- 既定で話し言葉優先
- 通訳としてそのまま口に出せる文へ寄せる
- 数値、否定、条件、依頼の強さは勝手に改変しない

2. 後処理段
- 翻訳結果から数値・単位表現を検出する
- 確信がある対象だけ換算と補足を付ける
- 英語出力では数値の読み方も併記する
- 曖昧な対象は原文維持を優先する

この機能のために OpenAI の別モデルを追加しない。理由は、翻訳品質と数値補足の責務を分離した方が遅延、コスト、失敗点の増加を抑えられるためである。

## Translation Style Policy

翻訳は既定で話し言葉優先にする。

求める出力特性は次のとおり。

- 書き言葉より、会話で自然な言い回しを優先する
- 不自然な逐語訳を避ける
- 必要に応じて文を短く保つ
- 発話の温度感に応じて丁寧さを保つ
- 口癖、言い淀み、重複は、意味を崩さない範囲で整理する
- 数字、単位、型番、否定、条件、納期、数量は崩さない

例:

- `ご確認いただけますでしょうか` より `確認してもらえますか`
- `本件につきましては` より `この件は`

## Output Annotation Policy

本文は通訳文として読みやすい形を維持し、補足情報は対象トークンの直後に括弧で付ける。

日本語出力例:

- `12 mm (0.47 in)`
- `35 psi (0.24 MPa / 2.41 bar)`
- `Ra 0.8 (Ra 0.8 um)`
- `#400 (約 35 um / FEPA P400)`

英語出力例:

- `12 mm (0.47 in, twelve millimeters)`
- `35 psi (0.24 MPa / 2.41 bar, thirty-five psi)`
- `Ra 0.8 (Ra 0.8 um, R-A zero point eight)`
- `#400 (about 35 microns / FEPA P400, number four hundred)`

表示ルールは次で固定する。

- 原発話の単位を先に出す
- 換算値は括弧内に追記する
- 英語出力だけ読み方を同じ括弧内に追加する
- 変換不能または曖昧なら無理に補足しない
- 丸めは会話中の実務利用を優先する

## Supported Conversion Domains

### Engineering Units

- 長さ: `mm / cm / m / in / ft`
- 重量: `g / kg / lb`
- 温度: `C / F`
- トルク: `Nm / lbf·ft`
- 圧力: `MPa / bar / psi`

### Surface Finish

- `Ra`

`Ra 0.8` のような表記は `Ra 0.8 um` 相当の補足を許容する。

### Abrasive Size

採用する基準は次のとおり。

- `JIS #`
- `FEPA P`
- `US Mesh / ASTM E11`
- `micron / um`

変換方針は次のとおり。

- `#400` は JIS 系として扱う
- `P400` は FEPA 系として扱う
- `mesh` は `US Mesh / ASTM E11` 基準で micron 補足へ変換する
- `micron / um` は可能なら JIS または FEPA へ逆引き補足する
- `grit` 単独は規格断定が危険なので原値維持を基本とする

## Ambiguity Handling

厳格な原則は `確信があるものだけ変換し、曖昧なら補足しない` とする。

ルール:

- 単位なし裸数字は換算しない
- 英語出力では、分類できた裸数字にだけ読み方を付ける
- `mesh` と `grit` は同一視しない
- `M8`, `A356`, `#2 line` のような識別子は変換しない
- 範囲表現は両端を換算する
- 公差表現は符号を保持して換算する
- 複合単位や未知単位は無理に展開しない

## Architecture

### Prompt Layer

`realtime_translator/prompts.py` の通常翻訳、2フェーズ翻訳、再翻訳プロンプトへ話し言葉優先の規約を追加する。

責務:

- 口語通訳文の出力方針を明示する
- 数値や条件を維持するように指示する
- ただし換算と読み方付与はここで直接やらせない

### Post-Processing Layer

新しい後処理モジュール `realtime_translator/translation_postprocess.py` を追加し、翻訳済みテキストに対して deterministic な補足を付ける。

最低限の公開 API は次とする。

- `annotate_translation(text: str, *, output_language: str, direction: str | None, source_stream_id: str, virtual_stream_id: str | None) -> str`
- `build_number_reading(token: str, output_language: str) -> str | None`
- `convert_engineering_token(token: str) -> AnnotationResult | None`
- `convert_abrasive_token(token: str) -> AnnotationResult | None`

責務:

- 数値と単位の検出
- 工学単位の換算
- `Ra` の補足
- `JIS # / FEPA P / mesh / micron` の相互補足
- 英語数値読みの付与

### Integration Point

翻訳結果が UI 履歴へ渡る前の段階で後処理を実行する。v1 の統合ポイントは次で固定する。

統合要件:

- 通常翻訳と 2 フェーズ翻訳の両方では、`realtime_translator/app.py` の `TranslatorApp._poll_queue()` 内 `translation_done` 分岐で、`TranslationHistory.append()` の直前に `annotate_translation(...)` を 1 回だけ適用する
- `translation_done` を発火する upstream は現行どおり `realtime_translator/api.py` の `ApiWorker._call_api()` と `realtime_translator/openai_llm.py` の `OpenAiLlmWorker._handle_phase0_2()` とし、ここでは後処理を入れず raw translation を UI キューへ流す
- 再翻訳では `realtime_translator/retranslation.py` の `RetranslationWorker._execute_retranslation()` の LLM 応答直後に `annotate_translation(...)` を適用してから `retrans_result` を返す
- 返答アシストと議事録生成には本後処理を適用しない
- 元の方向判定や履歴格納契約を壊さない
- 同一テキストへ二重適用しない

`translation_done` 系経路は `app.py` 側、`retrans_result` 経路は `retranslation.py` 側に固定することで、Gemini / OpenAI / OpenRouter の backend 差分による適用漏れを防ぐ。

## Number Reading Policy for English

英語出力では数値の読み方を常時付与する。

対象:

- 単位付き数値
- 裸数字。ただし `話される数値` と判定できるものに限る
- 小数
- 範囲
- 公差

除外対象:

- 型番、品番、識別子
  - 例: `M8`, `A356`, `AB-120`, `#2 line`
- 日付、時刻、バージョン、図面番号
  - 例: `2026-03-20`, `10:30`, `v2.1.0`, `DWG-004`
- 規格名や規格コードの一部として固定解釈される文字列
  - 例: `ISO 2768`, `JIS B 0601`

判定原則:

- 後処理が `数量・寸法・圧力・温度・粗さ・粒度` として token を分類できた場合だけ、英語読みを付ける
- 裸数字単独でも、文脈上の数量表現と判定できる場合にのみ読みを付ける
- 分類できない裸数字は原値維持とし、読みも付けない

読みの基本方針:

- `12` -> `twelve`
- `0.8` -> `zero point eight`
- `12.5 mm` -> `twelve point five millimeters`
- `#400` -> `number four hundred`
- `Ra 0.8` -> `R-A zero point eight`

## Conversion Tables and Data Sources

離散値の相互変換は実装者の裁量で埋めない。再現性確保のため、v1 ではリポジトリ内の固定テーブルを正本にする。

要件:

- `realtime_translator/unit_tables.py` を追加し、`JIS # / FEPA P / US Mesh / micron` の対応表を定数として保持する
- `mesh` は `US Mesh / ASTM E11` 基準の代表値テーブルを採用する
- `JIS #` と `FEPA P` は近似対応であることをテーブルコメントに明記する
- `micron / um` から `JIS #` と `FEPA P` への逆引きは、完全一致または定義済み近傍値へのみ許可する
- `grit` 単独はこのテーブルにマップしない

このテーブルは unit test で固定し、実装中に暗黙の丸めや線形補間を追加しない。

## Rounding Policy

会話用途を優先して、必要以上に細かい桁を出さない。

初期方針:

- 長さ: 小数 2 桁まで
- 温度: 小数 1 桁から 2 桁
- 圧力: 小数 2 桁まで
- トルク: 小数 2 桁まで
- 粗さ: 既存値維持、必要時のみ適度に丸める
- micron 補足: 整数または実務上自然な桁

補足:

- `Ra` は v1 では `um` 補足までを対象とし、inch 系粗さ換算は行わない
- `Ra 0.8` の補足は `Ra 0.8 um` のような単位明示を目的とし、別指標への変換ではない
- `Rz`, `Ry`, `Rmax` など他の粗さ指標は v1 の対象外とする

## Failure Behavior

後処理が失敗しても翻訳本文は捨てない。

要件:

- 補足生成に失敗したら原翻訳をそのまま返す
- 数値部分だけの部分失敗なら、成功した対象だけ補足する
- 例外を UI 全体エラーへ昇格させない

## Test Strategy

最低限のテスト観点は次とする。

- 話し言葉優先の指示が通常翻訳、2フェーズ翻訳、再翻訳の各プロンプトに入る
- 単位換算が対象ごとに正しく付く
- 英語出力で読み方が常時付く
- `Ra`, `#`, `P`, `mesh`, `um` の補足が期待どおり付く
- 曖昧ケースで誤変換しない
- 後処理失敗時に翻訳本文を保持する
- 離散テーブルの代表値が固定され、意図しない変更で落ちる
- 英語数字読みで識別子、日付、バージョン番号を誤注釈しない
- 話し言葉化で丁寧さが崩れすぎないことを prompt テスト観点に含める

回帰確認方法:

- prompt 変更は文字列レベルの unit test で検証し、`spoken` 指示と `数値を崩さない` 指示の同居を固定する
- 後処理は pure function の unit test で検証し、LLM の非決定性に依存しない
- 統合テストは LLM 呼び出しを mock し、`translation_done` と `retrans_result` の各経路で注釈が 1 回だけ適用されることを確認する
- 人手の翻訳品質確認が必要な場合でも、done 判定は deterministic test の通過を先に満たす

## Done Criteria

実装完了の判定は次で固定する。

- `realtime_translator/prompts.py` の通常翻訳、2フェーズ翻訳、再翻訳プロンプトに話し言葉優先ポリシーが反映されている
- `realtime_translator/translation_postprocess.py` と `realtime_translator/unit_tables.py` が追加され、変換テーブルがコードベースに保存されている
- `TranslatorApp._poll_queue()` の `translation_done` 分岐で注釈後テキストが `TranslationHistory` へ保存される
- `RetranslationWorker._execute_retranslation()` で再翻訳結果にも同じ注釈が 1 回だけ適用される
- 既存の返答アシスト、議事録、方向判定、自動方向モードが退行していない
- 関連 unit test / integration test が追加され、既存 test suite が通過する

## Risks

- `grit` の規格断定ミス
- `mesh` の実務慣習差による期待不一致
- 数字読み付与が長文で冗長になりすぎること
- 話し言葉化で丁寧さが落ちすぎること

## Implementation Recommendation

実装順は次を推奨する。

1. プロンプトを話し言葉優先へ変更する
2. 単位・数値補足の後処理モジュールを追加する
3. 通常翻訳、2フェーズ翻訳、再翻訳へ共通適用する
4. 単体テストで変換表と曖昧ケースを固定する
5. 統合テストで既存の翻訳フローを壊していないことを確認する
```
