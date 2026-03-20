"""auto モードの方向確定ヘルパー。"""

from dataclasses import dataclass


def normalize_stt_language(code: str | None) -> str | None:
    if not code:
        return None

    normalized = code.strip().lower()
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("ja"):
        return "ja"
    return None


def resolve_direction_from_stt_language(code: str | None) -> tuple[str, str] | tuple[None, None]:
    normalized = normalize_stt_language(code)
    if normalized == "en":
        return "en_ja", "stt_metadata"
    if normalized == "ja":
        return "ja_en", "stt_metadata"
    return None, None


@dataclass
class DirectionHeaderEvent:
    resolved_direction: str
    remainder: str


@dataclass
class AutoTranslationChunkEvent:
    resolved_direction: str
    translation_text: str


def parse_direction_header(text: str) -> DirectionHeaderEvent:
    parser = DirectionHeaderParser()
    event = parser.feed(text)
    if event is None:
        raise ValueError("direction header is incomplete")
    return event


class DirectionHeaderParser:
    def __init__(self) -> None:
        self._buffer = ""
        self._resolved_direction: str | None = None

    @property
    def resolved_direction(self) -> str | None:
        return self._resolved_direction

    def feed(self, chunk: str) -> DirectionHeaderEvent | None:
        self._buffer += chunk
        line_break_index = self._buffer.find("\n")
        if line_break_index == -1:
            return None

        header = self._buffer[:line_break_index].rstrip("\r").strip()
        remainder = self._buffer[line_break_index + 1:]

        if header == "DIRECTION: en_ja":
            self._resolved_direction = "en_ja"
        elif header == "DIRECTION: ja_en":
            self._resolved_direction = "ja_en"
        else:
            raise ValueError(f"invalid direction header: {header}")

        self._buffer = remainder
        return DirectionHeaderEvent(
            resolved_direction=self._resolved_direction,
            remainder=remainder,
        )


class AutoTranslationParser:
    _TRANSLATION_PREFIX = "TRANSLATION:"

    def __init__(self) -> None:
        self._direction_parser = DirectionHeaderParser()
        self._resolved_direction: str | None = None
        self._prefix_buffer = ""
        self._translation_started = False

    @property
    def resolved_direction(self) -> str | None:
        return self._resolved_direction

    def feed(self, chunk: str) -> AutoTranslationChunkEvent | None:
        if self._resolved_direction is None:
            header_event = self._direction_parser.feed(chunk)
            if header_event is None:
                return None
            self._resolved_direction = header_event.resolved_direction
            chunk = header_event.remainder

        translation_text = self._consume_translation_text(chunk)
        return AutoTranslationChunkEvent(
            resolved_direction=self._resolved_direction,
            translation_text=translation_text,
        )

    def _consume_translation_text(self, chunk: str) -> str:
        if self._translation_started:
            return chunk

        self._prefix_buffer += chunk
        stripped = self._prefix_buffer.lstrip()
        if not stripped:
            return ""

        if stripped.startswith(self._TRANSLATION_PREFIX):
            after_prefix = stripped[len(self._TRANSLATION_PREFIX):].lstrip()
            if not after_prefix:
                return ""
            self._translation_started = True
            self._prefix_buffer = ""
            return after_prefix

        if self._TRANSLATION_PREFIX.startswith(stripped):
            return ""

        self._translation_started = True
        self._prefix_buffer = ""
        return stripped
