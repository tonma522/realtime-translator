"""プロンプト生成"""
from .constants import SILENCE_SENTINEL, STREAM_LANGS


def build_prompt(stream_id: str, context: str) -> str:
    """通常モード: 音声→STT+翻訳 (phase=0)"""
    src, dst = STREAM_LANGS[stream_id]
    return (
        "あなたはリアルタイム翻訳アシスタントです。\n"
        f"【コンテキスト】{context}\n\n"
        f"音声を聞き、{src}を{dst}に翻訳してください。\n"
        "出力形式（必ずこの形式で）:\n"
        f"  原文: ({src}テキスト)\n"
        f"  訳文: ({dst}テキスト)\n\n"
        f"無音・聞き取れない場合は「{SILENCE_SENTINEL}」とだけ返してください。"
    )


def build_stt_prompt(stream_id: str) -> str:
    """2フェーズ Phase1: 音声→文字起こしのみ (phase=1)"""
    src, _ = STREAM_LANGS[stream_id]
    return (
        f"この音声を{src}でそのまま文字起こししてください。翻訳は不要です。"
        f"無音・聞き取れない場合は「{SILENCE_SENTINEL}」とだけ返してください。"
    )


def build_translation_prompt(stream_id: str, context: str, transcript: str) -> str:
    """2フェーズ Phase2: テキスト→翻訳のみ (phase=2)"""
    src, dst = STREAM_LANGS[stream_id]
    return (
        f"【コンテキスト】{context}\n"
        f"次の{src}テキストを{dst}に翻訳してください。\n"
        f"テキスト: {transcript}\n"
        "出力形式:\n  訳文: (翻訳結果のみ)"
    )
