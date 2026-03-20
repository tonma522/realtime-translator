"""翻訳履歴データ構造"""
import threading
from dataclasses import dataclass


@dataclass
class HistoryEntry:
    seq: int
    stream_id: str
    timestamp: str
    original: str
    translation: str
    virtual_stream_id: str | None = None
    resolved_direction: str | None = None
    error: str | None = None

    @property
    def usable_for_downstream(self) -> bool:
        return self.error is None


class TranslationHistory:
    """スレッドセーフな翻訳履歴ストア"""

    def __init__(self) -> None:
        self._entries: list[HistoryEntry] = []
        self._next_seq = 1
        self._lock = threading.Lock()

    def append(
        self,
        stream_id: str,
        timestamp: str,
        original: str,
        translation: str,
        virtual_stream_id: str | None = None,
        resolved_direction: str | None = None,
        error: str | None = None,
    ) -> HistoryEntry:
        with self._lock:
            entry = HistoryEntry(
                seq=self._next_seq,
                stream_id=stream_id,
                timestamp=timestamp,
                original=original,
                translation=translation,
                virtual_stream_id=virtual_stream_id or stream_id,
                resolved_direction=resolved_direction,
                error=error,
            )
            self._entries.append(entry)
            self._next_seq += 1
            return entry

    def get_range(self, center_seq: int, n_before: int, n_after: int) -> list[HistoryEntry]:
        with self._lock:
            center_idx = None
            for i, e in enumerate(self._entries):
                if e.seq == center_seq:
                    center_idx = i
                    break
            if center_idx is None:
                return []
            start = max(0, center_idx - n_before)
            end = min(len(self._entries), center_idx + n_after + 1)
            return list(self._entries[start:end])

    def get_by_seq(self, seq: int) -> HistoryEntry | None:
        with self._lock:
            for e in self._entries:
                if e.seq == seq:
                    return e
            return None

    def all_entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._next_seq = 1
