"""定数・設定値"""
from pathlib import Path
from .stream_modes import (
    STREAM_DIRECTION_LANGS,
    STREAM_MODE_DEFAULTS,
    STREAM_SOURCE_LABELS,
    STREAM_SOURCE_TAGS,
    TRANSLATION_MODE_LABELS,
    resolve_virtual_stream_id,
)

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    pyaudio = None
    PYAUDIO_AVAILABLE = False

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
    GENAI_AVAILABLE = False

try:
    import webrtcvad as _webrtcvad
    WEBRTCVAD_AVAILABLE = True
except ImportError:
    _webrtcvad = None
    WEBRTCVAD_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    WHISPER_AVAILABLE = False

try:
    import openai as _openai
    OPENAI_AVAILABLE = True
except ImportError:
    _openai = None
    OPENAI_AVAILABLE = False

CONFIG_PATH = Path.home() / ".realtime_translator_config.json"
LOG_PATH = Path.home() / ".realtime_translator.log"
GEMINI_MODEL = "gemini-2.5-flash"
MIN_API_INTERVAL_SEC = 4.0   # Free tier 15RPM
MIN_API_INTERVAL_BY_BACKEND: dict[str, float] = {
    "gemini": 4.0,      # Free tier 15RPM
    "openai": 0.5,      # Rate limit generous
    "openrouter": 0.5,  # Rate limit generous
    "whisper": 0.0,     # Local, no rate limit
}
API_QUEUE_MAXSIZE = 3
VAD_SILENCE_SECONDS = 0.5
AUDIO_CHUNK_SIZE = 1024
SILENCE_RMS_THRESHOLD = 200      # ループバック向け
MIC_SILENCE_RMS_THRESHOLD = 150  # マイク誤検知防止
SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM = 2 bytes per sample
SILENCE_SENTINEL = "<silent>"

# OpenAI / OpenRouter
OPENAI_CHAT_MODEL = "gpt-4o"
OPENROUTER_DEFAULT_MODEL = "google/gemini-2.0-flash-001"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# OpenAI STT
OPENAI_STT_MODELS = ("whisper-1",)
OPENAI_STT_DEFAULT_MODEL = "whisper-1"

STREAM_LANGS: dict[str, tuple[str, str]] = {
    stream_id: STREAM_DIRECTION_LANGS[mode]
    for stream_id, mode in STREAM_MODE_DEFAULTS.items()
}
STREAM_LANGS.update({
    resolve_virtual_stream_id(stream_id, mode): STREAM_DIRECTION_LANGS[mode]
    for stream_id in STREAM_MODE_DEFAULTS
    for mode in TRANSLATION_MODE_LABELS
})

_PTT_BINDINGS = ("<KeyPress-space>", "<KeyRelease-space>", "<FocusOut>")
_STREAM_META: dict[str, tuple[str, str, str]] = {
    stream_id: (
        STREAM_SOURCE_LABELS[stream_id],
        STREAM_SOURCE_TAGS[stream_id],
        TRANSLATION_MODE_LABELS[mode],
    )
    for stream_id, mode in STREAM_MODE_DEFAULTS.items()
}
_STREAM_META.update({
    resolve_virtual_stream_id(stream_id, mode): (
        STREAM_SOURCE_LABELS[stream_id],
        STREAM_SOURCE_TAGS[stream_id],
        TRANSLATION_MODE_LABELS[mode],
    )
    for stream_id in STREAM_MODE_DEFAULTS
    for mode in TRANSLATION_MODE_LABELS
})
