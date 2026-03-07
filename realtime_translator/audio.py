"""音声キャプチャ"""
import io
import logging
import threading
import wave

from .constants import (
    AUDIO_CHUNK_SIZE,
    SILENCE_RMS_THRESHOLD,
    pyaudio,
)
from .audio_utils import is_silent_pcm
from .vad import VoiceActivityDetector


class AudioCapture:
    """loopback / マイク両対応の汎用音声キャプチャクラス"""

    def __init__(self, device_index: int, chunk_seconds: int, callback,
                 label: str = "audio", pa=None,
                 ptt_event: threading.Event | None = None,
                 use_vad: bool = False,
                 silence_threshold: int = SILENCE_RMS_THRESHOLD):
        self.device_index = device_index
        self.chunk_seconds = chunk_seconds
        self.callback = callback
        self.label = label
        self._pa = pa
        self._ptt_event = ptt_event
        self._use_vad = use_vad
        self._silence_threshold = silence_threshold
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._record_loop, name=f"AudioCapture-{self.label}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running and self._thread is None:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _record_loop(self) -> None:
        own_pa = self._pa is None
        pa = pyaudio.PyAudio() if own_pa else self._pa
        try:
            info = pa.get_device_info_by_index(self.device_index)
            sample_rate = int(info["defaultSampleRate"])
            channels = int(info.get("maxInputChannels", 0)) or int(info.get("maxOutputChannels", 0)) or 2
            frames_needed = sample_rate * self.chunk_seconds
            stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                input=True, input_device_index=self.device_index,
                frames_per_buffer=AUDIO_CHUNK_SIZE,
            )

            frames: list[bytes] = []
            total_frames = 0
            was_ptt_active = False

            # VAD用 (PTTモードでは使わない)
            use_vad_mode = self._use_vad and self._ptt_event is None
            vad = VoiceActivityDetector(sample_rate) if use_vad_mode else None
            speech_frames: list[bytes] = []
            silent_count = 0
            silence_trigger = max(1, int(sample_rate * 0.8 / AUDIO_CHUNK_SIZE))
            max_speech_chunks = int(sample_rate * self.chunk_seconds * 2 / AUDIO_CHUNK_SIZE)

            while self._running:
                try:
                    data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
                    if self._ptt_event is not None:
                        # PTTモード
                        ptt_active = self._ptt_event.is_set()
                        if ptt_active:
                            frames.append(data)
                            was_ptt_active = True
                        elif was_ptt_active:
                            if frames and not is_silent_pcm(frames, self._silence_threshold):
                                wav_bytes = self._to_wav(frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    logging.exception("[%s] callback error (PTT)", self.label)
                            frames = []
                            was_ptt_active = False
                    elif use_vad_mode:
                        # VADモード: フレーム単位で発話検出
                        is_sp = vad.is_speech(data)
                        if is_sp:
                            speech_frames.append(data)
                            silent_count = 0
                        elif speech_frames:
                            silent_count += 1
                            speech_frames.append(data)
                            if silent_count >= silence_trigger:
                                wav_bytes = self._to_wav(speech_frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    logging.exception("[%s] callback error (VAD)", self.label)
                                speech_frames = []
                                silent_count = 0
                        if len(speech_frames) >= max_speech_chunks:
                            if speech_frames:
                                wav_bytes = self._to_wav(speech_frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    logging.exception("[%s] callback error (VAD max)", self.label)
                                speech_frames = []
                                silent_count = 0
                    else:
                        # 連続モード: チャンク単位
                        frames.append(data)
                        total_frames += AUDIO_CHUNK_SIZE
                        if total_frames >= frames_needed:
                            if not is_silent_pcm(frames, self._silence_threshold):
                                wav_bytes = self._to_wav(frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    logging.exception("[%s] callback error (continuous)", self.label)
                            frames = []
                            total_frames = 0
                except Exception:
                    logging.exception("[%s] audio stream error", self.label)
                    break

            stream.stop_stream()
            stream.close()
        finally:
            if own_pa:
                pa.terminate()

    @staticmethod
    def _to_wav(frames: list[bytes], channels: int, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    # 後方互換: テスト等から AudioCapture._is_silent_pcm で呼べるように
    _is_silent_pcm = staticmethod(is_silent_pcm)
