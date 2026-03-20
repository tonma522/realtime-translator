# Left Column Session Config Implementation Plan

作成日: 2026-03-20

## 目的

- 左カラム下部へ `セッション構成` と `クイック操作` を実装する
- 上部の `聴く (PC音声)` / `話す (マイク)` と主 CTA を維持したまま、左下の余白を解消する
- `クリア` / `エクスポート` をヘッダーから左下へ移し、状態更新契機を `TranslatorApp` 側で一元管理する

## 変更対象

- `realtime_translator\main_controls_panel.py`
- `realtime_translator\ui_state.py`
- `realtime_translator\app.py`
- `tests\test_main_controls_panel.py`
- 必要に応じて `tests\test_ui_state.py`

## 実装方針

1. `SessionSummary` を拡張する
   - 方向サマリー
   - デバイスサマリー
   - バックエンドサマリー
   - 構成更新時刻

2. `MainControlsPanel` を拡張する
   - ヘッダーから `クリア` / `エクスポート` を削除する
   - 下部に `セッション構成` 表示を追加する
   - 下部に `クイック操作` と補助文ラベルを追加する
   - ボタンの活性状態と補助文を外部から更新できる API を用意する

3. `TranslatorApp` で要約値とクイック操作状態を組み立てる
   - `trace` と明示更新で `構成更新時刻` を更新する
   - デバイス名 / バックエンド名 / 方向表示を `SessionSummary` に流す
   - `デバイス再読込` / `結果クリア` / `エクスポート` の有効条件と補助文を一元化する
   - エクスポート失敗時は左下補助文と中央状態バーの両方へ反映する

4. TDD で段階的に反映する
   - まず `MainControlsPanel` の新 UI 契約テストを追加する
   - 失敗確認後、最小実装を入れる
   - 次に `SessionSummary` / `TranslatorApp` の関連テストを追加または更新する
   - 最後に関連テストをまとめて再実行する

## 検証

- `pytest tests/test_main_controls_panel.py`
- 必要に応じて `pytest tests/test_ui_state.py`
- 実装完了後に関連テストを再実行して、失敗から成功へ反転したことを確認する
