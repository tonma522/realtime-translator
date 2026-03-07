"""Voice Activity Detection"""
from .constants import WEBRTCVAD_AVAILABLE, _webrtcvad


class VoiceActivityDetector:
    """webrtcvad ベースの発話区間検出。非対応時は RMS フォールバック"""
    FRAME_MS = 30  # webrtcvad は 10/20/30ms フレームのみ対応

    def __init__(self, sample_rate: int, aggressiveness: int = 2):
        self._sr = sample_rate
        self._frame_bytes = int(sample_rate * self.FRAME_MS / 1000) * 2
        if WEBRTCVAD_AVAILABLE and sample_rate in (8000, 16000, 32000, 48000):
            self._vad = _webrtcvad.Vad(aggressiveness)
        else:
            self._vad = None  # 44.1kHz 等は RMS フォールバック

    def is_speech(self, pcm_bytes: bytes) -> bool:
        if self._vad is None:
            # RMS フォールバック: audio.py の _is_silent_pcm を遅延import
            from .audio import AudioCapture
            return not AudioCapture._is_silent_pcm([pcm_bytes])
        frame = pcm_bytes[:self._frame_bytes]
        if len(frame) < self._frame_bytes:
            return False
        return self._vad.is_speech(frame, self._sr)
