"""録音モード戦略クラス (PTT / VAD / 連続)"""
import threading
from typing import Protocol

from .audio_utils import frames_to_wav, is_silent_pcm
from .constants import AUDIO_CHUNK_SIZE
from .vad import VoiceActivityDetector


def _emit_frames(frames: list[bytes], channels: int,
                  sample_rate: int, silence_threshold: int) -> bytes | None:
    """無音でなければ WAV bytes を返す共通ヘルパー"""
    if not frames or is_silent_pcm(frames, silence_threshold):
        return None
    return frames_to_wav(frames, channels, sample_rate)


class RecordStrategy(Protocol):
    """録音モードのプロトコル"""

    def process_frame(self, data: bytes) -> bytes | None:
        """1フレーム処理。完成チャンクがあれば WAV bytes を返す。"""
        ...

    def flush(self) -> bytes | None:
        """バッファ残りを WAV bytes で返す（将来用）。"""
        ...


class ContinuousStrategy:
    """連続モード: 一定フレーム数ごとにチャンク送出"""

    def __init__(self, frames_needed: int, channels: int,
                 sample_rate: int, silence_threshold: int) -> None:
        self._frames_needed = frames_needed
        self._channels = channels
        self._sample_rate = sample_rate
        self._silence_threshold = silence_threshold
        self._frames: list[bytes] = []
        self._total_frames = 0

    def process_frame(self, data: bytes) -> bytes | None:
        self._frames.append(data)
        self._total_frames += AUDIO_CHUNK_SIZE
        if self._total_frames >= self._frames_needed:
            result = self._emit()
            self._reset()
            return result
        return None

    def flush(self) -> bytes | None:
        if self._frames:
            result = self._emit()
            self._reset()
            return result
        return None

    def _emit(self) -> bytes | None:
        return _emit_frames(self._frames, self._channels, self._sample_rate, self._silence_threshold)

    def _reset(self) -> None:
        self._frames = []
        self._total_frames = 0


class PTTStrategy:
    """プッシュ・トゥ・トーク: PTT押下中に蓄積、離した時に送出"""

    def __init__(self, ptt_event: threading.Event, channels: int,
                 sample_rate: int, silence_threshold: int) -> None:
        self._ptt_event = ptt_event
        self._channels = channels
        self._sample_rate = sample_rate
        self._silence_threshold = silence_threshold
        self._frames: list[bytes] = []
        self._was_ptt_active = False

    def process_frame(self, data: bytes) -> bytes | None:
        ptt_active = self._ptt_event.is_set()
        if ptt_active:
            self._frames.append(data)
            self._was_ptt_active = True
            return None
        elif self._was_ptt_active:
            result = self._emit()
            self._reset()
            return result
        return None

    def flush(self) -> bytes | None:
        if self._frames:
            result = self._emit()
            self._reset()
            return result
        return None

    def _emit(self) -> bytes | None:
        return _emit_frames(self._frames, self._channels, self._sample_rate, self._silence_threshold)

    def _reset(self) -> None:
        self._frames = []
        self._was_ptt_active = False


class VADStrategy:
    """VADモード: 発話区間検出で自動分割"""

    def __init__(self, vad: VoiceActivityDetector, sample_rate: int,
                 channels: int, chunk_seconds: int,
                 silence_threshold: int) -> None:
        self._vad = vad
        self._channels = channels
        self._sample_rate = sample_rate
        self._silence_threshold = silence_threshold
        self._speech_frames: list[bytes] = []
        self._silent_count = 0
        from .constants import VAD_SILENCE_SECONDS
        self._silence_trigger = max(1, int(sample_rate * VAD_SILENCE_SECONDS / AUDIO_CHUNK_SIZE))
        self._max_speech_chunks = max(
            int(sample_rate * chunk_seconds * 2 / AUDIO_CHUNK_SIZE),
            int(sample_rate * 4 / AUDIO_CHUNK_SIZE),  # 最低4秒分を保証
        )

    def process_frame(self, data: bytes) -> bytes | None:
        is_sp = self._vad.is_speech(data)
        if is_sp:
            self._speech_frames.append(data)
            self._silent_count = 0
        elif self._speech_frames:
            self._silent_count += 1
            self._speech_frames.append(data)
            if self._silent_count >= self._silence_trigger:
                return self._emit_and_reset()

        # max-length check runs on EVERY frame
        if len(self._speech_frames) >= self._max_speech_chunks:
            if self._speech_frames:
                return self._emit_and_reset()
        return None

    def flush(self) -> bytes | None:
        if self._speech_frames:
            return self._emit_and_reset()
        return None

    def _emit_and_reset(self) -> bytes | None:
        frames = self._speech_frames
        self._speech_frames = []
        self._silent_count = 0
        return _emit_frames(frames, self._channels, self._sample_rate, self._silence_threshold)
