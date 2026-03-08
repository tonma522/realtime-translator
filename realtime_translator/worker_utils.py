"""Shared helpers for worker queue patterns."""
import logging
import queue
import threading


def enqueue_dropping_oldest(
    q: queue.Queue, item, label: str = "",
    lock: threading.Lock | None = None,
) -> bool:
    """Put *item* into *q*, dropping the oldest entry if full.

    This is the standard "backpressure via drop-oldest" pattern used by
    both :class:`ApiWorker` and :class:`WhisperWorker`.

    When *lock* is provided the entire check-drop-put sequence is atomic.
    Returns ``True`` on success, ``False`` on failure.
    """
    if lock is not None:
        lock.acquire()
    try:
        if q.full():
            try:
                q.get_nowait()
                if label:
                    logging.debug("[%s] queue full, dropped oldest request", label)
            except queue.Empty:
                pass
        try:
            q.put_nowait(item)
            return True
        except queue.Full:
            return False
    finally:
        if lock is not None:
            lock.release()


def send_stop_sentinel(q: queue.Queue, lock: threading.Lock | None = None) -> None:
    """Put a ``None`` sentinel into *q*, dropping oldest if full."""
    enqueue_dropping_oldest(q, None, lock=lock)
