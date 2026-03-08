"""Tests for OpenAiSttWorker: lifecycle, transcription, pipeline, errors."""

import queue
import time
import threading
import unittest
from unittest.mock import MagicMock

from realtime_translator.openai_stt import OpenAiSttWorker


class FakeApiWorker:
    def __init__(self):
        self.submitted = []
        self._running = True

    @property
    def is_running(self):
        return self._running

    def submit(self, req):
        self.submitted.append(req)


def _mock_client(text="Hello world"):
    client = MagicMock()
    response = MagicMock()
    response.text = text
    client.audio.transcriptions.create.return_value = response
    return client


def _make_worker(client=None, **kwargs):
    ui_q = queue.Queue()
    listen_worker = FakeApiWorker()
    speak_worker = FakeApiWorker()
    defaults = dict(
        api_worker_listen=listen_worker,
        api_worker_speak=speak_worker,
        ui_queue=ui_q,
        client=client or _mock_client(),
        context="test context",
    )
    defaults.update(kwargs)
    w = OpenAiSttWorker(**defaults)
    return w, ui_q, listen_worker, speak_worker


class TestLifecycle(unittest.TestCase):
    def test_start_sets_running(self):
        w, _, _, _ = _make_worker()
        w.start()
        self.assertTrue(w._running)
        w.stop()

    def test_stop_clears_thread(self):
        w, _, _, _ = _make_worker()
        w.start()
        w.stop()
        self.assertIsNone(w._thread)
        self.assertFalse(w._running)

    def test_submit_when_not_running_is_noop(self):
        w, _, _, _ = _make_worker()
        w.submit(b"wav", "listen")
        self.assertTrue(w._req_queue.empty())

    def test_signal_stop_then_join(self):
        w, _, _, _ = _make_worker()
        w.start()
        w.signal_stop()
        w.join(timeout=3)
        self.assertIsNone(w._thread)

    def test_status_message_on_start(self):
        w, ui_q, _, _ = _make_worker()
        w.start()
        time.sleep(0.3)
        w.stop()
        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        statuses = [m for m in messages if m[0] == "status"]
        self.assertTrue(any("STT" in s[1] for s in statuses))


class TestTranscription(unittest.TestCase):
    def test_transcript_sent_to_ui_queue(self):
        client = _mock_client("Hello world")
        w, ui_q, _, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        transcripts = [m for m in messages if m[0] == "transcript"]
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0][1], "listen")
        self.assertEqual(transcripts[0][3], "Hello world")

    def test_phase2_submitted_to_api_worker(self):
        client = _mock_client("Hello world")
        w, _, listen_w, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()

        self.assertEqual(len(listen_w.submitted), 1)
        req = listen_w.submitted[0]
        self.assertEqual(req.phase, 2)
        self.assertEqual(req.stream_id, "listen")
        self.assertIsNone(req.wav_bytes)
        self.assertEqual(req.transcript, "Hello world")

    def test_speak_stream_routes_to_speak_worker(self):
        client = _mock_client("こんにちは")
        w, _, _, speak_w = _make_worker(client=client)
        w.start()
        w.submit(b"wav_data", "speak")
        time.sleep(0.5)
        w.stop()

        self.assertEqual(len(speak_w.submitted), 1)
        self.assertEqual(speak_w.submitted[0].stream_id, "speak")

    def test_empty_transcript_no_phase2(self):
        client = _mock_client("   ")
        w, ui_q, listen_w, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()

        self.assertEqual(len(listen_w.submitted), 0)
        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        transcripts = [m for m in messages if m[0] == "transcript"]
        self.assertEqual(len(transcripts), 0)

    def test_blank_transcript_no_phase2(self):
        client = _mock_client("")
        w, _, listen_w, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()
        self.assertEqual(len(listen_w.submitted), 0)

    def test_language_passed_to_api(self):
        client = _mock_client("text")
        w, _, _, _ = _make_worker(client=client, language="ja")
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()

        call_kwargs = client.audio.transcriptions.create.call_args
        self.assertEqual(call_kwargs.kwargs.get("language"), "ja")

    def test_no_language_omits_param(self):
        client = _mock_client("text")
        w, _, _, _ = _make_worker(client=client, language=None)
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()

        call_kwargs = client.audio.transcriptions.create.call_args
        self.assertNotIn("language", call_kwargs.kwargs)

    def test_model_passed_to_api(self):
        client = _mock_client("text")
        w, _, _, _ = _make_worker(client=client, model="whisper-1")
        w.start()
        w.submit(b"wav_data", "listen")
        time.sleep(0.5)
        w.stop()

        call_kwargs = client.audio.transcriptions.create.call_args
        self.assertEqual(call_kwargs.kwargs.get("model"), "whisper-1")


class TestErrorHandling(unittest.TestCase):
    def test_api_error_produces_error_message(self):
        client = MagicMock()
        client.audio.transcriptions.create.side_effect = RuntimeError("API failure")
        w, ui_q, _, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav", "listen")
        time.sleep(0.5)
        w.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        errors = [m for m in messages if m[0] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][1], "listen")
        self.assertIn("API failure", errors[0][2])

    def test_error_does_not_crash_worker(self):
        client = MagicMock()
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient")
            resp = MagicMock()
            resp.text = "recovered"
            return resp

        client.audio.transcriptions.create.side_effect = side_effect
        w, ui_q, listen_w, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav1", "listen")
        time.sleep(0.3)
        w.submit(b"wav2", "listen")
        time.sleep(0.5)
        w.stop()

        self.assertEqual(len(listen_w.submitted), 1)


class TestQueueOverflow(unittest.TestCase):
    def test_overflow_drops_oldest(self):
        w, _, _, _ = _make_worker()
        w._running = True
        for i in range(3):
            w._req_queue.put_nowait((f"wav{i}".encode(), "listen"))
        self.assertTrue(w._req_queue.full())
        w.submit(b"new", "listen")
        items = []
        while not w._req_queue.empty():
            items.append(w._req_queue.get_nowait())
        wav_data = [it[0] for it in items]
        self.assertNotIn(b"wav0", wav_data)
        self.assertIn(b"new", wav_data)


class TestStopSentinel(unittest.TestCase):
    def test_none_exits_loop(self):
        w, _, _, _ = _make_worker()
        w._running = True
        w._req_queue.put(None)

        done = threading.Event()
        def run():
            w._worker_loop()
            done.set()
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(done.wait(timeout=3.0))
        t.join(timeout=1.0)


class TestWorkerNotRunningGuard(unittest.TestCase):
    def test_phase2_not_submitted_when_api_worker_stopped(self):
        client = _mock_client("text")
        w, _, listen_w, _ = _make_worker(client=client)
        listen_w._running = False
        w.start()
        w.submit(b"wav", "listen")
        time.sleep(0.5)
        w.stop()
        self.assertEqual(len(listen_w.submitted), 0)


class TestPendingRequests(unittest.TestCase):
    def test_pending_requests_decrements_on_drop(self):
        w, _, _, _ = _make_worker()
        w._running = True
        w.submit(b"a", "listen")
        w.submit(b"b", "listen")
        w.submit(b"c", "listen")  # queue full (maxsize=3)
        w.submit(b"d", "listen")  # drops oldest
        self.assertEqual(w._pending_requests, 3)

    def test_pending_requests_reaches_zero_after_processing(self):
        client = _mock_client("Hello")
        w, _, _, _ = _make_worker(client=client)
        w.start()
        w.submit(b"wav", "listen")
        time.sleep(0.5)
        w.stop()
        self.assertEqual(w._pending_requests, 0)

if __name__ == "__main__":
    unittest.main()
