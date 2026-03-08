"""音声処理のテスト"""
import math
import struct

from realtime_translator.audio import AudioCapture
from realtime_translator.audio_utils import is_silent_pcm
from realtime_translator.constants import SILENCE_RMS_THRESHOLD
from realtime_translator.vad import VoiceActivityDetector


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
        frames = _make_sine_pcm(amplitude=100)
        assert AudioCapture._is_silent_pcm(frames, threshold=SILENCE_RMS_THRESHOLD) is True
        assert AudioCapture._is_silent_pcm(frames, threshold=10) is False

    def test_above_threshold_returns_false(self):
        amplitude = int(SILENCE_RMS_THRESHOLD * math.sqrt(2)) + 50
        frames = _make_sine_pcm(amplitude=amplitude)
        assert AudioCapture._is_silent_pcm(frames, threshold=SILENCE_RMS_THRESHOLD) is False

    def test_multiple_frames_concatenated(self):
        """複数フレームが結合されてRMS計算される"""
        silent = _make_silent_pcm(512)
        loud = _make_sine_pcm(512, amplitude=10000)
        assert is_silent_pcm(silent + loud) is False

    def test_audio_utils_matches_audiocapture(self):
        """audio_utils.is_silent_pcm と AudioCapture._is_silent_pcm は同じ関数"""
        frames = _make_sine_pcm(amplitude=5000)
        assert is_silent_pcm(frames) == AudioCapture._is_silent_pcm(frames)


class TestVoiceActivityDetector:
    def test_supported_rate_uses_webrtcvad(self):
        """16kHz ではwebrtcvadが使用される（利用可能時）"""
        vad = VoiceActivityDetector(16000)
        from realtime_translator.constants import WEBRTCVAD_AVAILABLE
        if WEBRTCVAD_AVAILABLE:
            assert vad._vad is not None
        else:
            assert vad._vad is None

    def test_unsupported_rate_falls_back_to_rms(self):
        """44.1kHz ではwebrtcvadが使えずRMSフォールバック"""
        vad = VoiceActivityDetector(44100)
        assert vad._vad is None

    def test_rms_fallback_detects_silence(self):
        """RMSフォールバックで無音を正しく検出"""
        vad = VoiceActivityDetector(44100)
        silent = _make_silent_pcm(1024)
        assert vad.is_speech(silent[0]) is False

    def test_rms_fallback_detects_speech(self):
        """RMSフォールバックで音声を正しく検出"""
        vad = VoiceActivityDetector(44100)
        loud = _make_sine_pcm(1024, amplitude=10000)
        assert vad.is_speech(loud[0]) is True

    def test_short_frame_returns_false(self):
        """フレームが短すぎる場合、webrtcvad対応レートでもFalse"""
        vad = VoiceActivityDetector(16000)
        assert vad.is_speech(b"\x00\x00") is False
