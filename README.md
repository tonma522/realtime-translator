# Realtime Translator

双方向リアルタイム音声翻訳ツール。PC音声(ループバック)を日本語に、マイク入力を英語に翻訳します。

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. Gemini APIキーの取得

[Google AI Studio](https://aistudio.google.com/apikey) からAPIキーを取得してください。

### 3. 起動

```bash
python -m realtime_translator
```

または `start.bat` をダブルクリック。

## 使い方

1. APIキーを入力
2. ループバックデバイス(PC音声キャプチャ用)とマイクを選択
3. 翻訳コンテキストを入力(専門用語の精度向上に効果的)
4. 「翻訳開始」をクリック

## モード

| モード | 説明 |
|--------|------|
| **通常** | 音声→文字起こし+翻訳を1回のAPI呼び出しで実行 |
| **2フェーズ** | STTと翻訳を分離して精度向上 |
| **PTT** | プッシュ・トゥ・トーク(スペースキーまたはボタン) |
| **VAD** | 発話区間を自動検出して翻訳 |
| **Whisper** | ローカルSTT + API翻訳(要 `faster-whisper`) |

## オプション依存

```bash
pip install webrtcvad        # より正確な発話区間検出
pip install faster-whisper    # ローカル音声認識
```

## APIキーの保存

APIキーはOS標準の資格情報マネージャー(Windows Credential Manager)に安全に保存されます。
