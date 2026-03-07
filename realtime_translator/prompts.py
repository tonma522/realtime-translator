"""プロンプト生成"""


def build_prompt(stream_id: str, context: str) -> str:
    """通常モード: 音声→STT+翻訳 (phase=0)"""
    if stream_id == "listen":
        src, dst, src_ex, dst_ex = "英語", "日本語", "英語テキスト", "日本語テキスト"
    else:
        src, dst, src_ex, dst_ex = "日本語", "英語", "日本語テキスト", "英語テキスト"
    return (
        "あなたはリアルタイム翻訳アシスタントです。\n"
        f"【コンテキスト】{context}\n\n"
        f"音声を聞き、{src}を{dst}に翻訳してください。\n"
        "出力形式（必ずこの形式で）:\n"
        f"  原文: ({src_ex})\n"
        f"  訳文: ({dst_ex})\n\n"
        "無音・聞き取れない場合は「(無音)」とだけ返してください。"
    )


def build_stt_prompt(stream_id: str) -> str:
    """2フェーズ Phase1: 音声→文字起こしのみ (phase=1)"""
    lang = "英語" if stream_id == "listen" else "日本語"
    return (
        f"この音声を{lang}でそのまま文字起こししてください。翻訳は不要です。"
        "無音・聞き取れない場合は「(無音)」とだけ返してください。"
    )


def build_translation_prompt(stream_id: str, context: str, transcript: str) -> str:
    """2フェーズ Phase2: テキスト→翻訳のみ (phase=2)"""
    src, dst = ("英語", "日本語") if stream_id == "listen" else ("日本語", "英語")
    return (
        f"【コンテキスト】{context}\n"
        f"次の{src}テキストを{dst}に翻訳してください。\n"
        f"テキスト: {transcript}\n"
        "出力形式:\n  訳文: (翻訳結果のみ)"
    )
