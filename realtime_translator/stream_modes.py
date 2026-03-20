"""翻訳モードと仮想ストリーム定義。"""

STREAM_MODE_DEFAULTS: dict[str, str] = {
    "listen": "en_ja",
    "speak": "ja_en",
}

VALID_TRANSLATION_MODES = {"en_ja", "ja_en", "auto"}

TRANSLATION_MODE_LABELS: dict[str, str] = {
    "en_ja": "英語→日本語",
    "ja_en": "日本語→英語",
    "auto": "同時翻訳",
}
TRANSLATION_MODE_LABEL_TO_ID: dict[str, str] = {
    label: mode for mode, label in TRANSLATION_MODE_LABELS.items()
}

STREAM_DIRECTION_LANGS: dict[str, tuple[str, str]] = {
    "en_ja": ("英語", "日本語"),
    "ja_en": ("日本語", "英語"),
    "auto": ("英語または日本語", "反対言語"),
}

STREAM_SOURCE_LABELS: dict[str, str] = {
    "listen": "PC音声",
    "speak": "マイク",
}

STREAM_SOURCE_TAGS: dict[str, str] = {
    "listen": "stream_listen",
    "speak": "stream_speak",
}


def normalize_translation_mode(value: object, default: str) -> str:
    return value if value in VALID_TRANSLATION_MODES else default


def resolve_virtual_stream_id(stream_id: str, mode: str) -> str:
    if stream_id not in STREAM_MODE_DEFAULTS:
        raise ValueError(f"unknown stream_id: {stream_id}")
    normalized_mode = normalize_translation_mode(mode, STREAM_MODE_DEFAULTS[stream_id])
    return f"{stream_id}_{normalized_mode}"


def split_stream_id(stream_id: str) -> tuple[str, str]:
    if stream_id in STREAM_MODE_DEFAULTS:
        return stream_id, STREAM_MODE_DEFAULTS[stream_id]

    for source_stream_id in STREAM_MODE_DEFAULTS:
        prefix = f"{source_stream_id}_"
        if stream_id.startswith(prefix):
            mode = stream_id[len(prefix):]
            normalized_mode = normalize_translation_mode(mode, "")
            if normalized_mode:
                return source_stream_id, normalized_mode
            break

    raise KeyError(stream_id)


def get_stream_languages(stream_id: str) -> tuple[str, str]:
    _, mode = split_stream_id(stream_id)
    return STREAM_DIRECTION_LANGS[mode]


def get_stream_meta(stream_id: str) -> tuple[str, str, str]:
    source_stream_id, mode = split_stream_id(stream_id)
    return (
        STREAM_SOURCE_LABELS[source_stream_id],
        STREAM_SOURCE_TAGS[source_stream_id],
        TRANSLATION_MODE_LABELS[mode],
    )


def is_auto_stream(stream_id: str) -> bool:
    try:
        _, mode = split_stream_id(stream_id)
    except KeyError:
        return False
    return mode == "auto"


def translation_mode_to_label(mode: str) -> str:
    normalized = normalize_translation_mode(mode, "en_ja")
    return TRANSLATION_MODE_LABELS[normalized]


def label_to_translation_mode(label: str, default: str) -> str:
    return TRANSLATION_MODE_LABEL_TO_ID.get(label, default)
