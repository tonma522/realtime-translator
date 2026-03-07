"""定数・設定値"""
from pathlib import Path

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

CONFIG_PATH = Path.home() / ".realtime_translator_config.json"
LOG_PATH = Path.home() / ".realtime_translator.log"
GEMINI_MODEL = "gemini-2.5-flash"
MIN_API_INTERVAL_SEC = 4.0   # Free tier 15RPM
API_QUEUE_MAXSIZE = 3
AUDIO_CHUNK_SIZE = 1024
SILENCE_RMS_THRESHOLD = 200      # ループバック向け
MIC_SILENCE_RMS_THRESHOLD = 500  # マイク誤検知防止
SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM = 2 bytes per sample
SILENCE_SENTINEL = "(無音)"

STREAM_LANGS: dict[str, tuple[str, str]] = {
    "listen": ("英語", "日本語"),
    "speak":  ("日本語", "英語"),
}

_PTT_BINDINGS = ("<KeyPress-space>", "<KeyRelease-space>", "<FocusOut>")
_STREAM_META: dict[str, tuple[str, str]] = {
    "listen": ("PC音声→日本語", "stream_listen"),
    "speak":  ("マイク→英語",   "stream_speak"),
}
