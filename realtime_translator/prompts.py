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


def build_reply_assist_prompt(context: str, history_block: str) -> str:
    """返答アシスト: 会話履歴から返答候補を3つ提案"""
    return (
        "You are a meeting assistant.\n"
        f"[Context] {context}\n\n"
        "Below is the recent conversation flow:\n\n"
        f"{history_block}\n\n"
        "Based on this conversation, suggest 3 possible replies "
        "that the Japanese speaker should say next.\n"
        "For each suggestion, provide both Japanese and English.\n\n"
        "Output format (strictly follow this):\n"
        "1. [日本語] ...\n"
        "   [English] ...\n"
        "2. [日本語] ...\n"
        "   [English] ...\n"
        "3. [日本語] ...\n"
        "   [English] ...\n"
    )


def build_minutes_prompt(
    context: str, history_block: str, previous_minutes: str = "",
) -> str:
    """議事録生成: 会話履歴から議事録を生成/追記"""
    if previous_minutes:
        mode_instruction = (
            "Below are the previous meeting minutes followed by new conversation.\n"
            "Append a summary of the new conversation to the existing minutes.\n"
            "Keep the existing content unchanged and add new sections.\n\n"
            f"--- Previous Minutes ---\n{previous_minutes}\n--- End Previous Minutes ---\n\n"
        )
    else:
        mode_instruction = "Create new meeting minutes from the conversation below.\n\n"
    return (
        "You are a meeting minutes writer.\n"
        f"[Context] {context}\n\n"
        f"{mode_instruction}"
        f"Conversation:\n{history_block}\n\n"
        "Write the minutes in Japanese. Use a chronological summary format with "
        "time-based sections and bullet points for key discussion items and decisions."
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
