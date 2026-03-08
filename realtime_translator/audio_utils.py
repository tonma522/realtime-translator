"""音声ユーティリティ（循環依存を避けるため独立モジュール）"""
import array
import base64
import io
import logging
import math
import wave

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


def frames_to_wav(frames: list[bytes], channels: int, sample_rate: int) -> bytes:
    """PCMフレームリストをWAVバイト列に変換"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def wav_to_base64(wav_bytes: bytes) -> str:
    """WAVバイト列をbase64文字列に変換（OpenAI/OpenRouter audio input用）"""
    return base64.b64encode(wav_bytes).decode("ascii")
