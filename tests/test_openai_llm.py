"""Tests for OpenAiLlmWorker: lifecycle, streaming, phase routing, errors."""

import queue
import threading
import time
import unittest
from unittest.mock import MagicMock

from realtime_translator.api import ApiRequest
from realtime_translator.constants import API_QUEUE_MAXSIZE, SILENCE_SENTINEL
from realtime_translator.openai_llm import (
    AUDIO_CAPABLE_MODELS,
    OpenAiLlmWorker,
    _build_messages,
    _localize_openai_error,
)


def _make_chunk(content):
    """Create a mock streaming chunk with choices[0].delta.content."""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


def _make_empty_choices_chunk():
    """Create a mock chunk with empty choices list (OpenRouter SSE comment)."""
    chunk = MagicMock()
    chunk.choices = []
    return chunk


def _mock_client(chunks_list):
    """Create a mock OpenAI client returning chunks from chat.completions.create."""
    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks_list)
    return client


class TestBuildMessages(unittest.TestCase):
    """Test _build_messages helper."""

    def test_text_only(self):
        msgs = _build_messages("hello", wav_bytes=None)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        content = msgs[0]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "hello")

    def test_with_audio(self):
        msgs = _build_messages("prompt", wav_bytes=b"\x00\x01\x02")
        content = msgs[0]["content"]
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "input_audio")
        self.assertEqual(content[1]["input_audio"]["format"], "wav")
        # base64 of b"\x00\x01\x02"
        self.assertIsInstance(content[1]["input_audio"]["data"], str)


class TestLocalizeOpenAiError(unittest.TestCase):
    """Test _localize_openai_error with various exception types."""

    def test_generic_exception(self):
        exc = RuntimeError("something went wrong")
        result = _localize_openai_error(exc)
        self.assertEqual(result, "something went wrong")

    def test_openai_not_installed(self):
        """When openai is not installed, should fall back to str(exc)."""
        exc = ValueError("test")
        # _localize_openai_error handles ImportError internally
        result = _localize_openai_error(exc)
        self.assertEqual(result, "test")


class TestWorkerLifecycle(unittest.TestCase):
    """Worker start/stop lifecycle."""

    def test_start_sets_running(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        self.assertFalse(worker.is_running)
        worker.start()
        self.assertTrue(worker.is_running)
        worker.stop()
        self.assertFalse(worker.is_running)

    def test_stop_joins_thread(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker.start()
        self.assertIsNotNone(worker._thread)
        worker.stop()
        self.assertIsNone(worker._thread)

    def test_submit_when_not_running_is_noop(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        req = ApiRequest(wav_bytes=b"x", prompt="p", stream_id="listen")
        worker.submit(req)
        self.assertTrue(worker._req_queue.empty())

    def test_signal_stop_then_join(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker.start()
        worker.signal_stop()
        self.assertFalse(worker.is_running)
        worker.join(timeout=3)
        self.assertIsNone(worker._thread)


class TestQueueOverflow(unittest.TestCase):
    """Queue overflow: oldest request should be dropped."""

    def test_overflow_drops_oldest(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker._running = True

        for i in range(API_QUEUE_MAXSIZE):
            worker.submit(ApiRequest(wav_bytes=b"x", prompt=f"p{i}", stream_id="listen"))
        self.assertTrue(worker._req_queue.full())

        extra = ApiRequest(wav_bytes=b"x", prompt="extra", stream_id="listen")
        worker.submit(extra)

        items = []
        while not worker._req_queue.empty():
            items.append(worker._req_queue.get_nowait())
        prompts = [item.prompt for item in items]
        self.assertNotIn("p0", prompts)
        self.assertIn("extra", prompts)


class TestPhase0Streaming(unittest.TestCase):
    """Phase 0: streaming partial messages via chat completions."""

    def test_streaming_partial_messages(self):
        chunks = [_make_chunk("Hello "), _make_chunk("world")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="translate", stream_id="listen", phase=0,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        types = [m[0] for m in messages]
        self.assertIn("partial_start", types)
        self.assertIn("partial", types)
        self.assertIn("partial_end", types)

        ps_idx = types.index("partial_start")
        p_idx = types.index("partial")
        pe_idx = types.index("partial_end")
        self.assertLess(ps_idx, p_idx)
        self.assertLess(p_idx, pe_idx)

    def test_streaming_text_content(self):
        chunks = [_make_chunk("foo"), _make_chunk("bar")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0,
        ))
        time.sleep(0.5)
        worker.stop()

        partials = []
        while not ui_q.empty():
            msg = ui_q.get_nowait()
            if msg[0] == "partial":
                partials.append(msg[2])
        self.assertEqual(partials, ["foo", "bar"])

    def test_phase2_no_audio(self):
        """Phase 2 should not include audio in messages."""
        chunks = [_make_chunk("translated")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="translate this",
            stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        call_args = client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages", call_args[1].get("messages"))
        content = messages[0]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")

    def test_empty_content_skipped(self):
        """Chunks with None/empty content should not produce partial messages."""
        chunks = [_make_chunk(None), _make_chunk("text"), _make_chunk("")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="p", stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        partials = []
        while not ui_q.empty():
            msg = ui_q.get_nowait()
            if msg[0] == "partial":
                partials.append(msg[2])
        self.assertEqual(partials, ["text"])

    def test_auto_stream_strips_direction_header_and_emits_metadata(self):
        chunks = [_make_chunk("DIRECTION: ja_en\nTRANSLATION: "), _make_chunk("Hello")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="translate", stream_id="speak_auto", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        partials = [m[2] for m in messages if m[0] == "partial"]
        self.assertEqual("".join(partials), "Hello")
        done = [m for m in messages if m[0] == "translation_done"][0]
        self.assertEqual(done[1], "speak")
        self.assertEqual(done[2], "speak_auto")
        self.assertEqual(done[3], "ja_en")
        self.assertEqual(done[6], "Hello")

    def test_auto_stream_direction_parse_failure_emits_incomplete_result(self):
        chunks = [_make_chunk("DIRECTION: invalid\nTRANSLATION: ???")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="translate", stream_id="listen_auto", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        done = [m for m in messages if m[0] == "translation_done"][0]
        self.assertEqual(done[1], "listen")
        self.assertEqual(done[2], "listen_auto")
        self.assertIsNone(done[3])
        self.assertEqual(done[7], "direction_parse_failed")


class TestOpenRouterSSEComments(unittest.TestCase):
    """OpenRouter may produce chunks with empty choices list."""

    def test_empty_choices_chunk_skipped(self):
        chunks = [
            _make_empty_choices_chunk(),
            _make_chunk("hello"),
            _make_empty_choices_chunk(),
        ]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="p", stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        partials = []
        while not ui_q.empty():
            msg = ui_q.get_nowait()
            if msg[0] == "partial":
                partials.append(msg[2])
        self.assertEqual(partials, ["hello"])

    def test_all_empty_choices_no_output(self):
        chunks = [_make_empty_choices_chunk(), _make_empty_choices_chunk()]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="p", stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("partial_start", types)
        self.assertNotIn("partial_end", types)


def _make_non_streaming_response(content):
    """Create a mock non-streaming response with choices[0].message.content."""
    response = MagicMock()
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response.choices = [choice]
    return response


class TestPhase1STT(unittest.TestCase):
    """Phase 1: STT → transcript + phase=2 auto-submission (non-streaming)."""

    def test_phase1_transcript_and_phase2(self):
        phase1_response = _make_non_streaming_response("Hello world")
        phase2_chunks = [_make_chunk("translated")]
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            phase1_response, iter(phase2_chunks),
        ]
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="stt prompt",
            stream_id="listen", phase=1, context="ctx",
        ))
        time.sleep(1.0)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        types = [m[0] for m in messages]
        self.assertIn("transcript", types)
        transcript_msg = [m for m in messages if m[0] == "transcript"][0]
        self.assertEqual(transcript_msg[3], "Hello world")
        self.assertIn("partial_start", types)
        self.assertIn("partial", types)
        self.assertIn("partial_end", types)

    def test_phase1_uses_non_streaming(self):
        """Phase 1 should use stream=False."""
        phase1_response = _make_non_streaming_response("text")
        phase2_chunks = [_make_chunk("ok")]
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            phase1_response, iter(phase2_chunks),
        ]
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="stt",
            stream_id="listen", phase=1, context="ctx",
        ))
        time.sleep(1.0)
        worker.stop()

        # First call should be non-streaming
        first_call = client.chat.completions.create.call_args_list[0]
        self.assertFalse(first_call.kwargs.get("stream", True))

    def test_silence_sentinel_no_phase2(self):
        response = _make_non_streaming_response(SILENCE_SENTINEL)
        client = MagicMock()
        client.chat.completions.create.return_value = response
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="stt",
            stream_id="listen", phase=1,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("transcript", types)

    def test_empty_transcript_no_phase2(self):
        response = _make_non_streaming_response("  ")
        client = MagicMock()
        client.chat.completions.create.return_value = response
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="stt",
            stream_id="listen", phase=1,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("transcript", types)


class TestAudioModelValidation(unittest.TestCase):
    """Non-audio-capable models should reject phase=0/1 with audio."""

    def test_phase0_non_audio_model_error(self):
        client = _mock_client([])
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o",  # not in AUDIO_CAPABLE_MODELS
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        error_msgs = [m for m in messages if m[0] == "error"]
        self.assertEqual(len(error_msgs), 1)
        self.assertIn("gpt-4o", error_msgs[0][2])
        self.assertIn("音声入力", error_msgs[0][2])

    def test_phase1_non_audio_model_error(self):
        client = _mock_client([])
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-mini",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="p", stream_id="listen", phase=1,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        error_msgs = [m for m in messages if m[0] == "error"]
        self.assertEqual(len(error_msgs), 1)

    def test_phase2_any_model_ok(self):
        """Phase 2 (text only) should work with any model."""
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-mini",  # not audio-capable
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="translate",
            stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertIn("partial_start", types)
        self.assertNotIn("error", types)

    def test_audio_capable_model_accepted(self):
        chunks = [_make_chunk("result")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertIn("partial_start", types)
        self.assertNotIn("error", types)


class TestErrorHandling(unittest.TestCase):
    """Error handling: API exception -> error message in UI queue."""

    def test_streaming_exception_produces_error(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("API failure")
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="p", stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        error_msgs = [m for m in messages if m[0] == "error"]
        self.assertEqual(len(error_msgs), 1)
        self.assertIn("API failure", error_msgs[0][2])

    def test_mid_stream_exception_sends_partial_end(self):
        def _failing_stream(**kwargs):
            yield _make_chunk("text")
            raise RuntimeError("mid-stream failure")

        client = MagicMock()
        client.chat.completions.create.side_effect = _failing_stream
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(
            wav_bytes=None, prompt="p", stream_id="listen", phase=2,
        ))
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertIn("partial_start", types)
        self.assertIn("partial_end", types)
        self.assertIn("error", types)


class TestRateLimiting(unittest.TestCase):
    """Rate limiting via _min_interval_sec."""

    def test_rate_limiting_delays_call(self):
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        interval = 0.3
        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=interval)
        worker._running = True
        worker._last_text_call_time = time.monotonic()

        req = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
        t0 = time.monotonic()
        worker._call_api(req)
        elapsed = time.monotonic() - t0

        self.assertGreaterEqual(elapsed, interval * 0.8)

    def test_text_call_time_updated_after_phase2(self):
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker._running = True
        before = time.monotonic()
        req = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
        worker._call_api(req)
        self.assertGreaterEqual(worker._last_text_call_time, before)

    def test_audio_call_time_updated_after_phase0(self):
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker._running = True
        before = time.monotonic()
        req = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0)
        worker._call_api(req)
        self.assertGreaterEqual(worker._last_audio_call_time, before)

    def test_last_call_time_updated_on_error(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("fail")
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker._running = True
        before = time.monotonic()
        req = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
        worker._call_api(req)
        self.assertGreaterEqual(worker._last_text_call_time, before)

    def test_phase1_then_phase2_no_rate_limit_delay(self):
        """Phase 1 (audio) then Phase 2 (text) use separate rate limit timers."""
        phase1_response = _make_non_streaming_response("transcript")
        phase2_chunks = [_make_chunk("ok")]
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            phase1_response, iter(phase2_chunks),
        ]
        ui_q = queue.Queue()

        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=10.0,
            model="gpt-4o-audio-preview",
        )
        worker._running = True

        # Phase 1 call
        req1 = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=1, context="ctx")
        worker._call_api(req1)
        # Phase 2 should NOT be rate-limited by phase 1's timer
        req2 = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
        t0 = time.monotonic()
        worker._call_api(req2)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0)


class TestStopSentinel(unittest.TestCase):
    """Stop sentinel (None) causes worker loop to exit."""

    def test_none_in_queue_exits_loop(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker._running = True
        worker._req_queue.put(None)

        done = threading.Event()

        def run_loop():
            worker._worker_loop()
            done.set()

        t = threading.Thread(target=run_loop)
        t.start()
        finished = done.wait(timeout=3.0)
        t.join(timeout=1.0)
        self.assertTrue(finished)


class TestLabelParameter(unittest.TestCase):
    """Label parameter for thread naming."""

    def test_custom_label(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0, label="MyLabel")
        worker.start()
        self.assertEqual(worker._thread.name, "MyLabel")
        worker.stop()

    def test_default_label(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker.start()
        self.assertEqual(worker._thread.name, "OpenAiLlmWorker")
        worker.stop()


class TestPendingRequests(unittest.TestCase):
    """pending_requests counter accuracy tests."""

    def test_pending_requests_decrements_on_drop(self):
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker._running = True
        for i in range(API_QUEUE_MAXSIZE):
            worker.submit(ApiRequest(wav_bytes=b"x", prompt=f"p{i}", stream_id="listen"))
        worker.submit(ApiRequest(wav_bytes=b"x", prompt="extra", stream_id="listen"))
        self.assertEqual(worker._pending_requests, API_QUEUE_MAXSIZE)

    def test_pending_requests_reaches_zero_after_processing(self):
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(ui_q, client=client, min_interval_sec=0)
        worker.start()
        worker.submit(ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2))
        time.sleep(0.5)
        worker.stop()
        self.assertEqual(worker._pending_requests, 0)

class TestPhase1SelfSubmitFullQueue(unittest.TestCase):
    """Phase 1 self-submit with full queue: pending_requests stays correct."""

    def test_phase1_self_submit_with_full_queue(self):
        phase1_response = _make_non_streaming_response("Hello world")
        phase2_chunks = [_make_chunk("translated")]
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            phase1_response, iter(phase2_chunks),
        ]
        ui_q = queue.Queue()
        worker = OpenAiLlmWorker(
            ui_q, client=client, min_interval_sec=0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        for i in range(API_QUEUE_MAXSIZE - 1):
            worker.submit(ApiRequest(wav_bytes=b"x", prompt=f"filler{i}", stream_id="listen"))
        worker.submit(ApiRequest(
            wav_bytes=b"wav", prompt="stt", stream_id="listen", phase=1, context="ctx"
        ))
        time.sleep(2.0)
        worker.stop()
        self.assertEqual(worker._pending_requests, 0)


if __name__ == "__main__":
    unittest.main()
