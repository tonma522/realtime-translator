"""音声キャプチャ"""
import logging
import threading

from .constants import (
    AUDIO_CHUNK_SIZE,
    SILENCE_RMS_THRESHOLD,
    pyaudio,
)
from .audio_utils import frames_to_wav, is_silent_pcm
from .record_strategies import (
    ContinuousStrategy,
    PTTStrategy,
    RecordStrategy,
    VADStrategy,
)
from .vad import VoiceActivityDetector


class AudioCapture:
    """loopback / マイク両対応の汎用音声キャプチャクラス"""

    def __init__(self, device_index: int, chunk_seconds: int, callback,
                 label: str = "audio", pa=None,
                 ptt_event: threading.Event | None = None,
                 use_vad: bool = False,
                 silence_threshold: int = SILENCE_RMS_THRESHOLD,
                 error_callback=None,
                 strategy: RecordStrategy | None = None):
        self.device_index = device_index
        self.chunk_seconds = chunk_seconds
        self.callback = callback
        self.label = label
        self._pa = pa
        self._ptt_event = ptt_event
        self._use_vad = use_vad
        self._silence_threshold = silence_threshold
        self._error_callback = error_callback
        self._strategy = strategy
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._record_loop, name=f"AudioCapture-{self.label}", daemon=True)
        self._thread.start()

    def signal_stop(self) -> None:
        self._running = False

    def join(self, timeout: float = 3) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stop(self) -> None:
        if not self._running and self._thread is None:
            return
        self.signal_stop()
        self.join(timeout=3)

    def _build_strategy(self, sample_rate: int, channels: int) -> RecordStrategy:
        """コンストラクタパラメータに基づき録音戦略を選択"""
        if self._ptt_event is not None:
            return PTTStrategy(
                self._ptt_event, channels, sample_rate, self._silence_threshold,
            )
        if self._use_vad:
            vad = VoiceActivityDetector(sample_rate)
            return VADStrategy(
                vad, sample_rate, channels, self.chunk_seconds,
                self._silence_threshold,
            )
        frames_needed = sample_rate * self.chunk_seconds
        return ContinuousStrategy(
            frames_needed, channels, sample_rate, self._silence_threshold,
        )

    def _safe_callback(self, wav_bytes: bytes) -> None:
        """コールバック呼び出し（例外をログして続行）"""
        try:
            self.callback(wav_bytes)
        except Exception:
            logging.exception("[%s] callback error", self.label)

    def _record_loop(self) -> None:
        own_pa = self._pa is None
        pa = pyaudio.PyAudio() if own_pa else self._pa
        try:
            info = pa.get_device_info_by_index(self.device_index)
            sample_rate = int(info["defaultSampleRate"])
            channels = int(info.get("maxInputChannels", 0)) or int(info.get("maxOutputChannels", 0)) or 2
            stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                input=True, input_device_index=self.device_index,
                frames_per_buffer=AUDIO_CHUNK_SIZE,
            )

            strategy = self._strategy or self._build_strategy(sample_rate, channels)

            while self._running:
                try:
                    data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
                except Exception as exc:
                    logging.exception("[%s] audio stream error", self.label)
                    if self._error_callback is not None:
                        try:
                            self._error_callback(
                                f"音声ストリームエラー ({self.label}): {exc}"
                            )
                        except Exception:
                            logging.exception("[%s] error_callback failed", self.label)
                    break
                try:
                    wav_bytes = strategy.process_frame(data)
                except Exception:
                    logging.exception("[%s] strategy error", self.label)
                    continue
                if wav_bytes:
                    self._safe_callback(wav_bytes)

            stream.stop_stream()
            stream.close()
        finally:
            if own_pa:
                pa.terminate()

    @staticmethod
    def _to_wav(frames: list[bytes], channels: int, sample_rate: int) -> bytes:
        return frames_to_wav(frames, channels, sample_rate)

    # 後方互換: テスト等から AudioCapture._is_silent_pcm で呼べるように
    _is_silent_pcm = staticmethod(is_silent_pcm)
