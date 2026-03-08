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

### faster-whisper（ローカル音声認識）

ローカルWhisper STTを使用するには `faster-whisper` をインストールしてください。

```bash
# CPU のみ（NVIDIA GPU がない場合）
pip install faster-whisper

# NVIDIA GPU（CUDA）を使用する場合
pip install faster-whisper[cuda]
```

**注意事項:**
- 初回起動時にモデルファイルが自動ダウンロードされます（small: 約500MB、medium: 約1.5GB）
- GPU利用には CUDA 12 + cuDNN 9 が必要です。未対応環境では自動的にCPUフォールバックします
- `small` モデルがデフォルトです。精度を上げたい場合は UI でモデルサイズを変更してください

### その他のオプション

```bash
pip install webrtcvad        # より正確な発話区間検出(VAD)
pip install openai           # OpenAI / OpenRouter バックエンド
```

### まとめてインストール

```bash
pip install "realtime-translator[all]"
# または個別に:
pip install "realtime-translator[whisper]"
pip install "realtime-translator[openai]"
pip install "realtime-translator[vad]"
```

## APIキーの保存

APIキーは可能な場合、OS標準の資格情報マネージャー(Windows Credential Manager)に保存されます。
keyring backend が利用できない環境では、設定ファイル(`~/.realtime_translator_config.json`)に平文で保存されます。

## 既知の制限事項

- **翻訳欠落**: APIリクエストキューは最大3件です。長い発話やAPI応答遅延時、古いリクエストは自動的に破棄されます。これはリアルタイム性を優先する仕様です。
- **レート制限**: Gemini API Free tierでは15RPM制限があります。「聴く」と「話す」を同時に有効にすると、合算でレート制限に達する場合があります。
- **VADサンプルレート**: webrtcvadは8/16/32/48kHzのみ対応。44.1kHz等のデバイスでは自動的にRMSベースのフォールバックに切り替わります。
