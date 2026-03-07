"""音声処理のテスト"""
import math
import struct

from realtime_translator.audio import AudioCapture
from realtime_translator.constants import SILENCE_RMS_THRESHOLD


def _make_silent_pcm(n_samples: int = 1024) -> list[bytes]:
    """無音PCMデータを生成"""
    return [struct.pack(f"<{n_samples}h", *([0] * n_samples))]


def _make_sine_pcm(n_samples: int = 1024, amplitude: int = 10000, freq: float = 440.0, sample_rate: int = 16000) -> list[bytes]:
    """正弦波PCMデータを生成"""
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate))
        for i in range(n_samples)
    ]
    return [struct.pack(f"<{n_samples}h", *samples)]


class TestIsSilentPcm:
    def test_silence_returns_true(self):
        frames = _make_silent_pcm()
        assert AudioCapture._is_silent_pcm(frames) is True

    def test_loud_signal_returns_false(self):
        frames = _make_sine_pcm(amplitude=10000)
        assert AudioCapture._is_silent_pcm(frames) is False

    def test_empty_frames_returns_true(self):
        assert AudioCapture._is_silent_pcm([b""]) is True

    def test_custom_threshold(self):
        # 小さい振幅: デフォルト閾値では無音、低い閾値では非無音
        frames = _make_sine_pcm(amplitude=100)
        assert AudioCapture._is_silent_pcm(frames, threshold=SILENCE_RMS_THRESHOLD) is True
        assert AudioCapture._is_silent_pcm(frames, threshold=10) is False

    def test_above_threshold_returns_false(self):
        # 閾値を明確に超える振幅で非無音判定
        amplitude = int(SILENCE_RMS_THRESHOLD * math.sqrt(2)) + 50
        frames = _make_sine_pcm(amplitude=amplitude)
        assert AudioCapture._is_silent_pcm(frames, threshold=SILENCE_RMS_THRESHOLD) is False
