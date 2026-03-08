"""プロンプト生成"""
from .constants import SILENCE_SENTINEL, STREAM_LANGS


def build_prompt(stream_id: str, context: str, show_original: bool = True) -> str:
    """通常モード: 音声→STT+翻訳 (phase=0)"""
    src, dst = STREAM_LANGS[stream_id]
    if show_original:
        output_fmt = (
            "Output format (strictly follow this):\n"
            f"  原文: ({src} text)\n"
            f"  訳文: ({dst} text)"
        )
    else:
        output_fmt = "Output only the translation."
    return (
        "You are a realtime translation assistant.\n"
        f"[Context] {context}\n\n"
        f"Listen to the audio and translate {src} to {dst}.\n"
        f"{output_fmt}\n\n"
        f"If silent or inaudible, respond with only \"{SILENCE_SENTINEL}\"."
    )


def build_stt_prompt(stream_id: str) -> str:
    """2フェーズ Phase1: 音声→文字起こしのみ (phase=1)"""
    src, _ = STREAM_LANGS[stream_id]
    return (
        f"Transcribe this audio in {src} exactly as spoken. Do not translate."
        f" If silent or inaudible, respond with only \"{SILENCE_SENTINEL}\"."
    )


def build_translation_prompt(stream_id: str, context: str, transcript: str) -> str:
    """2フェーズ Phase2: テキスト→翻訳のみ (phase=2)"""
    src, dst = STREAM_LANGS[stream_id]
    return (
        f"[Context] {context}\n"
        f"Translate the following {src} text to {dst}.\n"
        f"Text: {transcript}\n"
        "Output only the translation."
    )


def build_retranslation_prompt(
    stream_id: str,
    context: str,
    history_block: str,
) -> str:
    """再翻訳プロンプト: 会話履歴ブロック + ターゲットマーカー付き"""
    src, dst = STREAM_LANGS[stream_id]
    return (
        "You are a realtime translation assistant.\n"
        f"[Context] {context}\n\n"
        "Below is the conversation flow. Re-translate the entry marked with "
        "\">>>\" using the surrounding context for better accuracy.\n\n"
        f"{history_block}\n\n"
        f"Re-translate the \">>>\" entry from {src} to {dst}.\n"
        "Output only the translation."
    )
