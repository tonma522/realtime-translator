"""録音戦略クラスのテスト"""
import math
import struct
import threading
import wave
import io

from unittest.mock import MagicMock

from realtime_translator.audio_utils import frames_to_wav
from realtime_translator.audio import AudioCapture
from realtime_translator.constants import AUDIO_CHUNK_SIZE, SILENCE_RMS_THRESHOLD
from realtime_translator.record_strategies import (
    ContinuousStrategy,
    PTTStrategy,
    VADStrategy,
)

CHANNELS = 1
SAMPLE_RATE = 16000


def _make_silent_pcm(n_samples: int = AUDIO_CHUNK_SIZE) -> bytes:
    """無音PCMデータ (1フレーム分)"""
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


def _make_loud_pcm(n_samples: int = AUDIO_CHUNK_SIZE, amplitude: int = 10000) -> bytes:
    """正弦波PCMデータ (1フレーム分)"""
    samples = [
        int(amplitude * math.sin(2 * math.pi * 440.0 * i / SAMPLE_RATE))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def _is_valid_wav(data: bytes) -> bool:
    """WAVバイト列の妥当性チェック"""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            return wf.getnchannels() >= 1 and wf.getsampwidth() == 2
    except Exception:
        return False


class TestFramesToWav:
    def test_produces_valid_wav(self):
        frames = [_make_loud_pcm()]
        result = frames_to_wav(frames, CHANNELS, SAMPLE_RATE)
        assert _is_valid_wav(result)

    def test_matches_audiocapture_to_wav(self):
        frames = [_make_loud_pcm()]
        assert frames_to_wav(frames, CHANNELS, SAMPLE_RATE) == AudioCapture._to_wav(frames, CHANNELS, SAMPLE_RATE)

    def test_wav_metadata(self):
        frames = [_make_loud_pcm()]
        result = frames_to_wav(frames, 2, 44100)
        with wave.open(io.BytesIO(result), "rb") as wf:
            assert wf.getnchannels() == 2
            assert wf.getframerate() == 44100
            assert wf.getsampwidth() == 2


class TestContinuousStrategy:
    def _make_strategy(self, frames_needed=None):
        if frames_needed is None:
            frames_needed = SAMPLE_RATE * 5  # 5秒
        return ContinuousStrategy(frames_needed, CHANNELS, SAMPLE_RATE, SILENCE_RMS_THRESHOLD)

    def test_accumulation_returns_none_before_threshold(self):
        s = self._make_strategy(frames_needed=AUDIO_CHUNK_SIZE * 3)
        assert s.process_frame(_make_loud_pcm()) is None
        assert s.process_frame(_make_loud_pcm()) is None

    def test_emits_wav_at_threshold(self):
        s = self._make_strategy(frames_needed=AUDIO_CHUNK_SIZE * 2)
        s.process_frame(_make_loud_pcm())
        result = s.process_frame(_make_loud_pcm())
        assert result is not None
        assert _is_valid_wav(result)

    def test_silence_filtered(self):
        s = self._make_strategy(frames_needed=AUDIO_CHUNK_SIZE)
        result = s.process_frame(_make_silent_pcm())
        assert result is None

    def test_resets_after_emission(self):
        s = self._make_strategy(frames_needed=AUDIO_CHUNK_SIZE)
        s.process_frame(_make_loud_pcm())
        # After emission, next frame should not immediately emit
        s2 = self._make_strategy(frames_needed=AUDIO_CHUNK_SIZE * 2)
        s2.process_frame(_make_loud_pcm())  # emits at 1 chunk
        result = s2.process_frame(_make_loud_pcm())
        assert result is not None
        # Third frame starts new accumulation
        assert s2.process_frame(_make_loud_pcm()) is None

    def test_flush_returns_buffered(self):
        s = self._make_strategy(frames_needed=AUDIO_CHUNK_SIZE * 10)
        s.process_frame(_make_loud_pcm())
        result = s.flush()
        assert result is not None
        assert _is_valid_wav(result)

    def test_flush_empty_returns_none(self):
        s = self._make_strategy()
        assert s.flush() is None


class TestPTTStrategy:
    def _make_strategy(self):
        ev = threading.Event()
        s = PTTStrategy(ev, CHANNELS, SAMPLE_RATE, SILENCE_RMS_THRESHOLD)
        return s, ev

    def test_no_emission_while_held(self):
        s, ev = self._make_strategy()
        ev.set()
        for _ in range(10):
            assert s.process_frame(_make_loud_pcm()) is None

    def test_press_release_emits_wav(self):
        s, ev = self._make_strategy()
        ev.set()
        s.process_frame(_make_loud_pcm())
        s.process_frame(_make_loud_pcm())
        ev.clear()
        result = s.process_frame(_make_loud_pcm())
        assert result is not None
        assert _is_valid_wav(result)

    def test_silent_ptt_filtered(self):
        s, ev = self._make_strategy()
        ev.set()
        s.process_frame(_make_silent_pcm())
        ev.clear()
        result = s.process_frame(_make_silent_pcm())
        assert result is None

    def test_no_emission_without_press(self):
        s, ev = self._make_strategy()
        for _ in range(5):
            assert s.process_frame(_make_loud_pcm()) is None

    def test_flush_emits_buffered(self):
        s, ev = self._make_strategy()
        ev.set()
        s.process_frame(_make_loud_pcm())
        s.process_frame(_make_loud_pcm())
        result = s.flush()
        assert result is not None
        assert _is_valid_wav(result)

    def test_flush_empty_returns_none(self):
        s, ev = self._make_strategy()
        assert s.flush() is None

    def test_multiple_press_release_cycles(self):
        s, ev = self._make_strategy()
        # Cycle 1
        ev.set()
        s.process_frame(_make_loud_pcm())
        ev.clear()
        r1 = s.process_frame(_make_loud_pcm())
        assert r1 is not None
        # Cycle 2
        ev.set()
        s.process_frame(_make_loud_pcm())
        ev.clear()
        r2 = s.process_frame(_make_loud_pcm())
        assert r2 is not None


class TestVADStrategy:
    def _make_strategy(self, chunk_seconds=5):
        vad = MagicMock()
        s = VADStrategy(vad, SAMPLE_RATE, CHANNELS, chunk_seconds, SILENCE_RMS_THRESHOLD)
        return s, vad

    def test_speech_then_silence_emits(self):
        s, vad = self._make_strategy()
        # Speech frames
        vad.is_speech.return_value = True
        s.process_frame(_make_loud_pcm())
        s.process_frame(_make_loud_pcm())
        # Silence frames (enough to trigger)
        vad.is_speech.return_value = False
        result = None
        for _ in range(s._silence_trigger + 1):
            r = s.process_frame(_make_loud_pcm())
            if r is not None:
                result = r
                break
        assert result is not None
        assert _is_valid_wav(result)

    def test_pure_silence_returns_none(self):
        s, vad = self._make_strategy()
        vad.is_speech.return_value = False
        for _ in range(20):
            assert s.process_frame(_make_silent_pcm()) is None

    def test_max_length_emits(self):
        s, vad = self._make_strategy(chunk_seconds=1)
        vad.is_speech.return_value = True
        result = None
        for _ in range(s._max_speech_chunks + 1):
            r = s.process_frame(_make_loud_pcm())
            if r is not None:
                result = r
                break
        assert result is not None
        assert _is_valid_wav(result)

    def test_flush_emits_buffered_speech(self):
        s, vad = self._make_strategy()
        vad.is_speech.return_value = True
        s.process_frame(_make_loud_pcm())
        s.process_frame(_make_loud_pcm())
        result = s.flush()
        assert result is not None
        assert _is_valid_wav(result)

    def test_flush_empty_returns_none(self):
        s, vad = self._make_strategy()
        assert s.flush() is None

    def test_silent_count_only_after_speech(self):
        """silent_count は speech_frames が非空の時のみインクリメント"""
        s, vad = self._make_strategy()
        vad.is_speech.return_value = False
        for _ in range(50):
            s.process_frame(_make_silent_pcm())
        assert s._silent_count == 0
        assert len(s._speech_frames) == 0
