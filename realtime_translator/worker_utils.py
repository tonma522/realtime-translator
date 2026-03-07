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


def stop_worker_thread(
    q: queue.Queue,
    thread, # threading.Thread | None
    timeout: float = 10,
) -> None:
    """Send a ``None`` sentinel into *q* and join *thread*.

    Returns the thread reference (always ``None`` after join).
    """
    try:
        q.put_nowait(None)
    except queue.Full:
        pass
    if thread:
        thread.join(timeout=timeout)
    return None
