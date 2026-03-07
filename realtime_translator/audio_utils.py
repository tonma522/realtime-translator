"""音声ユーティリティ（循環依存を避けるため独立モジュール）"""
import array
import logging
import math

from .constants import SAMPLE_WIDTH_BYTES, SILENCE_RMS_THRESHOLD


def is_silent_pcm(frames: list[bytes], threshold: int = SILENCE_RMS_THRESHOLD) -> bool:
    """生PCMフレームのRMS振幅がthreshold以下ならTrue"""
    pcm = b"".join(frames)
    n = len(pcm) // SAMPLE_WIDTH_BYTES
    if n == 0:
        return True
    samples = array.array("h", pcm[:n * SAMPLE_WIDTH_BYTES])
    rms = math.sqrt(sum(s * s for s in samples) / n)
    logging.debug("[VAD] RMS=%.1f threshold=%d", rms, threshold)
    return rms < threshold
