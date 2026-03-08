"""Shared helpers for worker queue patterns."""
import logging
import queue


def enqueue_dropping_oldest(q: queue.Queue, item, label: str = "") -> None:
    """Put *item* into *q*, dropping the oldest entry if full.

    This is the standard "backpressure via drop-oldest" pattern used by
    both :class:`ApiWorker` and :class:`WhisperWorker`.
    """
    if q.full():
        try:
            q.get_nowait()
            if label:
                logging.debug("[%s] queue full, dropped oldest request", label)
        except queue.Empty:
            pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def send_stop_sentinel(q: queue.Queue) -> None:
    """Put a ``None`` sentinel into *q*, silently dropping if full."""
    try:
        q.put_nowait(None)
    except queue.Full:
        pass
