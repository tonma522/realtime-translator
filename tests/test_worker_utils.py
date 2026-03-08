"""Tests for worker_utils queue helpers."""
import queue
import threading

from realtime_translator.worker_utils import enqueue_dropping_oldest, send_stop_sentinel


class TestEnqueueDroppingOldest:
    def test_returns_true_on_success(self):
        q = queue.Queue(maxsize=3)
        assert enqueue_dropping_oldest(q, "item1") is True
        assert q.qsize() == 1

    def test_drops_oldest_when_full(self):
        q = queue.Queue(maxsize=2)
        q.put("a")
        q.put("b")
        result = enqueue_dropping_oldest(q, "c", label="test")
        assert result is True
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items == ["b", "c"]

    def test_concurrent_safety(self):
        q = queue.Queue(maxsize=3)
        lock = threading.Lock()
        errors = []

        def producer(n):
            try:
                for i in range(100):
                    enqueue_dropping_oldest(q, f"{n}-{i}", lock=lock)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=producer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert q.qsize() <= 3

    def test_without_lock_no_crash(self):
        q = queue.Queue(maxsize=2)
        q.put("x")
        q.put("y")
        result = enqueue_dropping_oldest(q, "z", lock=None)
        assert result is True

    def test_returns_false_never(self):
        """With normal Queue, put_nowait after drop should always succeed."""
        q = queue.Queue(maxsize=1)
        q.put("a")
        result = enqueue_dropping_oldest(q, "b")
        assert result is True


class TestSendStopSentinel:
    def test_when_full(self):
        q = queue.Queue(maxsize=2)
        q.put("a")
        q.put("b")
        send_stop_sentinel(q)
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert None in items

    def test_when_empty(self):
        q = queue.Queue(maxsize=2)
        send_stop_sentinel(q)
        assert q.get_nowait() is None

    def test_with_lock(self):
        q = queue.Queue(maxsize=1)
        q.put("x")
        lock = threading.Lock()
        send_stop_sentinel(q, lock=lock)
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert None in items
