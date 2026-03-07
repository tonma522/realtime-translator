"""音声ユーティリティ（循環依存を避けるため独立モジュール）"""
import array
import logging
import math

from .constants import SILENCE_RMS_THRESHOLD


def is_silent_pcm(frames: list[bytes], threshold: int = SILENCE_RMS_THRESHOLD) -> bool:
    """生PCMフレームのRMS振幅がthreshold以下ならTrue"""
    pcm = b"".join(frames)
    n = len(pcm) // 2
    if n == 0:
        return True
    samples = array.array("h", pcm[:n * 2])
    rms = math.sqrt(sum(s * s for s in samples) / n)
    logging.debug("[VAD] RMS=%.1f threshold=%d", rms, threshold)
    return rms < threshold
