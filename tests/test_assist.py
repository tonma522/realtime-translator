"""AssistWorker のテスト"""
import queue
import time
import threading
from unittest.mock import MagicMock

import pytest

import realtime_translator.assist as assist_module
from realtime_translator.assist import (
    AssistWorker, AssistRequest, MAX_HISTORY_ENTRIES, MAX_HISTORY_CHARS,
    _StopSentinel, _STOP,
)
from realtime_translator.history import TranslationHistory


@pytest.fixture(autouse=True)
def _reset_seq_counter():
    """Reset the global _seq_counter before each test to prevent test leak."""
    assist_module._seq_counter = 0


class FakeMonitoredWorker:
    def __init__(self, pending=0, busy=False):
        self._pending_requests = pending
        self._is_busy = busy

    @property
    def pending_requests(self):
        return self._pending_requests

    @property
    def is_busy(self):
        return self._is_busy


def _make_history(n=5):
    h = TranslationHistory()
    for i in range(n):
        sid = "listen" if i % 2 == 0 else "speak"
        h.append(sid, f"12:00:{i:02d}", f"original_{i}", f"translation_{i}")
    return h


def _make_mock_client(response_text="mock response"):
    client = MagicMock()
    # Gemini mock
    mock_response = MagicMock()
    mock_response.text = response_text
    client.models.generate_content.return_value = mock_response
    # OpenAI mock
    mock_choice = MagicMock()
    mock_choice.message.content = response_text
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    client.chat.completions.create.return_value = mock_completion
    return client


def _make_worker(history=None, monitored=None, backend="gemini", client=None, **kwargs):
    ui_queue = queue.Queue()
    if history is None:
        history = _make_history()
    if monitored is None:
        monitored = [FakeMonitoredWorker()]
    if client is None:
        client = _make_mock_client()
    worker = AssistWorker(
        ui_queue=ui_queue,
        history=history,
        monitored_workers=monitored,
        llm_backend=backend,
        model="test-model",
        api_key="test-key",
        min_interval_sec=0.0,
        client_factory=lambda: client,
        **kwargs,
    )
    return worker, ui_queue


class TestSubmit:
    def test_returns_request_id(self):
        worker, _ = _make_worker()
        rid = worker.submit("reply_assist", "ctx")
        assert isinstance(rid, str)
        assert len(rid) == 8

    def test_unique_ids(self):
        worker, _ = _make_worker()
        ids = {worker.submit("reply_assist", "ctx") for _ in range(10)}
        assert len(ids) == 10


class TestReplyAssist:
    def test_execution(self):
        worker, ui_queue = _make_worker()
        worker.start()
        rid = worker.submit("reply_assist", "test context")
        time.sleep(1.0)
        worker.stop()

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_result" and r[1] == rid and r[2] == "reply_assist" for r in results)


class TestMinutes:
    def test_execution(self):
        worker, ui_queue = _make_worker()
        worker.start()
        rid = worker.submit("minutes", "test context")
        time.sleep(1.0)
        worker.stop()

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_result" and r[1] == rid and r[2] == "minutes" for r in results)

    def test_with_previous_minutes(self):
        client = _make_mock_client("updated minutes")
        worker, ui_queue = _make_worker(client=client)
        worker.start()
        rid = worker.submit("minutes", "ctx", previous_minutes="previous content")
        time.sleep(1.0)
        worker.stop()

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_result" and r[3] == "updated minutes" for r in results)


class TestIdleCheck:
    def test_blocks_when_busy(self):
        busy_worker = FakeMonitoredWorker(pending=1, busy=True)
        worker, ui_queue = _make_worker(monitored=[busy_worker])
        worker.start()
        rid = worker.submit("reply_assist", "ctx")
        time.sleep(0.5)
        # Should not have produced result yet
        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert not any(r[0] == "assist_result" for r in results)
        # Now make idle
        busy_worker._pending_requests = 0
        busy_worker._is_busy = False
        time.sleep(1.0)
        worker.stop()
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_result" and r[1] == rid for r in results)

    def test_idle_includes_stt_workers(self):
        stt = FakeMonitoredWorker(pending=1, busy=True)
        llm = FakeMonitoredWorker(pending=0, busy=False)
        worker, _ = _make_worker(monitored=[llm, stt])
        assert not worker._is_idle()
        stt._pending_requests = 0
        stt._is_busy = False
        assert worker._is_idle()


class TestPriority:
    def test_assist_over_minutes(self):
        """reply_assist should be processed before minutes when both queued"""
        client = _make_mock_client()
        call_order = []
        original_generate = client.models.generate_content

        def track_call(*args, **kwargs):
            prompt = args[0] if args else kwargs.get("contents", [""])[0]
            if "suggest 3 possible replies" in str(prompt):
                call_order.append("reply_assist")
            else:
                call_order.append("minutes")
            return original_generate(*args, **kwargs)

        client.models.generate_content.side_effect = track_call

        # Use busy worker to queue both before processing
        busy = FakeMonitoredWorker(pending=1, busy=True)
        worker, ui_queue = _make_worker(monitored=[busy], client=client)
        worker.start()

        # Submit minutes first, then assist
        worker.submit("minutes", "ctx")
        worker.submit("reply_assist", "ctx")
        time.sleep(0.3)

        # Release
        busy._pending_requests = 0
        busy._is_busy = False
        time.sleep(2.0)
        worker.stop()

        assert len(call_order) >= 2
        assert call_order[0] == "reply_assist"

    def test_same_priority_fifo(self):
        """Same priority requests should be FIFO"""
        client = _make_mock_client()
        call_count = [0]
        results = []

        def track_call(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.text = f"response_{call_count[0]}"
            return resp

        client.models.generate_content.side_effect = track_call

        busy = FakeMonitoredWorker(pending=1, busy=True)
        worker, ui_queue = _make_worker(monitored=[busy], client=client)
        worker.start()

        rid1 = worker.submit("reply_assist", "ctx1")
        rid2 = worker.submit("reply_assist", "ctx2")
        time.sleep(0.3)

        busy._pending_requests = 0
        busy._is_busy = False
        time.sleep(2.0)
        worker.stop()

        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())

        assist_results = [r for r in results if r[0] == "assist_result"]
        assert len(assist_results) >= 2
        assert assist_results[0][1] == rid1
        assert assist_results[1][1] == rid2


class TestLifecycle:
    def test_start_stop(self):
        worker, _ = _make_worker()
        worker.start()
        worker.stop()
        assert worker._thread is None

    def test_stop_without_start(self):
        worker, _ = _make_worker()
        worker.stop()  # should not crash


class TestEdgeCases:
    def test_empty_history_error(self):
        empty_history = TranslationHistory()
        worker, ui_queue = _make_worker(history=empty_history)
        worker.start()
        rid = worker.submit("reply_assist", "ctx")
        time.sleep(1.0)
        worker.stop()

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_error" and r[1] == rid and "空" in r[3] for r in results)

    def test_empty_response_becomes_error(self):
        client = _make_mock_client("")
        worker, ui_queue = _make_worker(client=client)
        worker.start()
        rid = worker.submit("reply_assist", "ctx")
        time.sleep(1.0)
        worker.stop()

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_error" and r[1] == rid for r in results)

    def test_history_truncation(self):
        """Large history is truncated"""
        h = TranslationHistory()
        # Add more than MAX_HISTORY_ENTRIES
        for i in range(MAX_HISTORY_ENTRIES + 50):
            h.append("listen", f"12:00:{i:02d}", f"orig {'x' * 100}", f"trans {'y' * 100}")
        entries = h.all_entries()
        truncated = AssistWorker._truncate_history(entries)
        assert len(truncated) <= MAX_HISTORY_ENTRIES

    def test_history_char_truncation(self):
        """History truncated by character count"""
        h = TranslationHistory()
        for i in range(100):
            h.append("listen", "12:00:00", "x" * 1000, "y" * 1000)
        entries = h.all_entries()
        truncated = AssistWorker._truncate_history(entries)
        total = sum(len(e.original) + len(e.translation) for e in truncated)
        assert total <= MAX_HISTORY_CHARS + 2100  # some overhead tolerance


class TestOpenAIBackend:
    def test_openai_execution(self):
        client = _make_mock_client("openai response")
        worker, ui_queue = _make_worker(backend="openai", client=client)
        worker.start()
        rid = worker.submit("reply_assist", "ctx")
        time.sleep(1.0)
        worker.stop()

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assert any(r[0] == "assist_result" and r[3] == "openai response" for r in results)


class TestStopSentinel:
    def test_sorts_after_requests(self):
        """_STOP should sort after all AssistRequest items."""
        pq = queue.PriorityQueue()
        pq.put(_STOP)
        req = AssistRequest(
            request_id="test", request_type="reply_assist",
            context="ctx", priority=0,
        )
        pq.put(req)
        first = pq.get_nowait()
        assert isinstance(first, AssistRequest)
        second = pq.get_nowait()
        assert isinstance(second, _StopSentinel)

    def test_stop_sentinel_comparison_with_itself(self):
        """Two _StopSentinel instances should be equal."""
        s1 = _StopSentinel()
        s2 = _StopSentinel()
        assert not (s1 < s2)
        assert s1 <= s2
        assert s1 == s2


class TestDrainUntilSentinel:
    def test_signal_stop_drains_pending_requests_before_exit(self):
        """Submit requests, then signal_stop() → worker exits cleanly."""
        client = _make_mock_client("response")
        busy = FakeMonitoredWorker(pending=1, busy=True)
        worker, ui_queue = _make_worker(monitored=[busy], client=client)
        worker.start()

        rid1 = worker.submit("reply_assist", "ctx1")
        rid2 = worker.submit("reply_assist", "ctx2")
        rid3 = worker.submit("reply_assist", "ctx3")
        time.sleep(0.2)

        # Release workers and signal stop
        busy._pending_requests = 0
        busy._is_busy = False
        worker.signal_stop()
        worker.join(timeout=10)

        results = []
        while not ui_queue.empty():
            results.append(ui_queue.get_nowait())
        assist_results = [r for r in results if r[0] == "assist_result"]
        assert len(assist_results) >= 1
        assert worker._thread is None

    def test_signal_stop_with_full_queue(self):
        """signal_stop() should deliver _STOP even when queue is full."""
        worker, ui_queue = _make_worker()
        worker.start()
        for i in range(20):
            worker.submit("reply_assist", f"ctx{i}")
        worker.signal_stop()
        worker.join(timeout=10)
        assert worker._thread is None
