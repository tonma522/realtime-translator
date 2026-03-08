"""Tests for TranslationHistory"""
import threading
from realtime_translator.history import TranslationHistory, HistoryEntry


class TestTranslationHistory:
    def test_append_and_all_entries(self):
        h = TranslationHistory()
        h.append("listen", "12:00:00", "Hello", "こんにちは")
        h.append("speak", "12:00:05", "はい", "Yes")
        entries = h.all_entries()
        assert len(entries) == 2
        assert entries[0].seq == 1
        assert entries[1].seq == 2
        assert entries[0].original == "Hello"
        assert entries[1].translation == "Yes"

    def test_get_by_seq(self):
        h = TranslationHistory()
        h.append("listen", "12:00:00", "Hello", "こんにちは")
        h.append("speak", "12:00:05", "はい", "Yes")
        entry = h.get_by_seq(2)
        assert entry is not None
        assert entry.original == "はい"
        assert h.get_by_seq(999) is None

    def test_get_range(self):
        h = TranslationHistory()
        for i in range(10):
            h.append("listen", f"12:00:{i:02d}", f"orig{i}", f"trans{i}")
        # Center on seq=5, 2 before, 2 after
        result = h.get_range(5, 2, 2)
        seqs = [e.seq for e in result]
        assert seqs == [3, 4, 5, 6, 7]

    def test_get_range_at_edges(self):
        h = TranslationHistory()
        for i in range(5):
            h.append("listen", "12:00:00", f"o{i}", f"t{i}")
        # Center on seq=1, ask for 3 before (only 0 available)
        result = h.get_range(1, 3, 3)
        seqs = [e.seq for e in result]
        assert seqs == [1, 2, 3, 4]

    def test_get_range_missing_seq(self):
        h = TranslationHistory()
        h.append("listen", "12:00:00", "a", "b")
        assert h.get_range(999, 1, 1) == []

    def test_clear(self):
        h = TranslationHistory()
        h.append("listen", "12:00:00", "Hello", "こんにちは")
        h.clear()
        assert h.all_entries() == []
        # Seq should reset
        e = h.append("listen", "12:00:01", "Hi", "やあ")
        assert e.seq == 1

    def test_thread_safety(self):
        h = TranslationHistory()
        errors = []

        def worker(start):
            try:
                for i in range(100):
                    h.append("listen", "12:00:00", f"orig-{start}-{i}", f"trans-{start}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(h.all_entries()) == 400

    def test_append_returns_entry(self):
        h = TranslationHistory()
        entry = h.append("listen", "12:00:00", "Hello", "こんにちは")
        assert isinstance(entry, HistoryEntry)
        assert entry.seq == 1
        assert entry.stream_id == "listen"
