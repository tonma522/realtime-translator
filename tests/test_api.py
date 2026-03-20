"""Tests for ApiWorker: rate limiting, phase routing, streaming, errors."""

import queue
import threading
import time
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, PropertyMock

from realtime_translator.api import ApiRequest, ApiWorker
from realtime_translator.constants import (
    API_QUEUE_MAXSIZE,
    SILENCE_SENTINEL,
)


def _make_chunk(text):
    """Create a mock chunk with .text attribute."""
    chunk = MagicMock()
    chunk.text = text
    return chunk


def _make_chunk_valueerror():
    """Create a mock chunk whose .text raises ValueError."""
    chunk = MagicMock()
    type(chunk).text = PropertyMock(side_effect=ValueError("no text"))
    return chunk


def _mock_client(chunks_list):
    """Create a mock genai client returning chunks_list from generate_content_stream."""
    client = MagicMock()
    client.models.generate_content_stream.return_value = iter(chunks_list)
    return client


class TestApiRequest(unittest.TestCase):
    """Verify ApiRequest dataclass defaults."""

    def test_defaults(self):
        req = ApiRequest(wav_bytes=b"data", prompt="p", stream_id="listen")
        self.assertEqual(req.phase, 0)
        self.assertEqual(req.context, "")
        self.assertEqual(req.transcript, "")

    def test_phase_2_no_wav(self):
        req = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
        self.assertIsNone(req.wav_bytes)
        self.assertEqual(req.phase, 2)


class TestWorkerLifecycle(unittest.TestCase):
    """Worker start/stop lifecycle."""

    def test_start_sets_running(self):
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        self.assertFalse(worker.is_running)
        worker.start()
        self.assertTrue(worker.is_running)
        worker.stop()
        self.assertFalse(worker.is_running)

    def test_stop_joins_thread(self):
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker.start()
        self.assertIsNotNone(worker._thread)
        worker.stop()
        self.assertIsNone(worker._thread)

    def test_stop_sentinel_exits_loop(self):
        """Putting None into the queue causes the worker loop to exit."""
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker.start()
        # The stop method puts None, which causes _worker_loop to break
        worker.stop()
        self.assertFalse(worker.is_running)
        # Thread should have exited
        self.assertIsNone(worker._thread)

    def test_submit_when_not_running_is_noop(self):
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        req = ApiRequest(wav_bytes=b"x", prompt="p", stream_id="listen")
        worker.submit(req)
        self.assertTrue(worker._req_queue.empty())


class TestQueueOverflow(unittest.TestCase):
    """Queue overflow: oldest request should be dropped."""

    def test_overflow_drops_oldest(self):
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker._running = True  # enable submit without starting thread

        # Fill queue to maxsize (3)
        reqs = []
        for i in range(API_QUEUE_MAXSIZE):
            r = ApiRequest(wav_bytes=b"x", prompt=f"p{i}", stream_id="listen")
            reqs.append(r)
            worker.submit(r)
        self.assertTrue(worker._req_queue.full())

        # Submit one more — should drop oldest (p0)
        extra = ApiRequest(wav_bytes=b"x", prompt="extra", stream_id="listen")
        worker.submit(extra)

        # Queue should still be full (maxsize items)
        self.assertEqual(worker._req_queue.qsize(), API_QUEUE_MAXSIZE)

        # Drain and check: p0 was dropped, we should have p1, p2, extra
        items = []
        while not worker._req_queue.empty():
            items.append(worker._req_queue.get_nowait())
        prompts = [item.prompt for item in items]
        self.assertNotIn("p0", prompts)
        self.assertIn("extra", prompts)

    def test_submit_4_items_to_maxsize_3(self):
        """Submit 4 items to maxsize=3 queue, verify oldest is dropped each time."""
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker._running = True

        for i in range(4):
            worker.submit(ApiRequest(wav_bytes=b"x", prompt=f"req{i}", stream_id="listen"))

        items = []
        while not worker._req_queue.empty():
            items.append(worker._req_queue.get_nowait())
        prompts = [it.prompt for it in items]
        self.assertEqual(len(prompts), API_QUEUE_MAXSIZE)
        # req0 should have been dropped
        self.assertNotIn("req0", prompts)
        self.assertIn("req3", prompts)


class TestPhase0Streaming(unittest.TestCase):
    """Phase 0: normal mode with wav_bytes, streaming partial messages."""

    def test_streaming_partial_messages(self):
        """Phase 0 should produce partial_start, partial, partial_end."""
        chunks = [_make_chunk("Hello "), _make_chunk("world")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="translate", stream_id="listen", phase=0
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

        # Check order: partial_start before partial before partial_end
        ps_idx = types.index("partial_start")
        p_idx = types.index("partial")
        pe_idx = types.index("partial_end")
        self.assertLess(ps_idx, p_idx)
        self.assertLess(p_idx, pe_idx)

    def test_streaming_includes_text(self):
        """Partial messages should contain the streamed text."""
        chunks = [_make_chunk("foo"), _make_chunk("bar")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0
            ))
            time.sleep(0.5)
            worker.stop()

        partials = []
        while not ui_q.empty():
            msg = ui_q.get_nowait()
            if msg[0] == "partial":
                partials.append(msg[2])

        self.assertEqual(partials, ["foo", "bar"])

    def test_empty_chunks_skipped(self):
        """Chunks with empty text should not produce partial messages."""
        chunks = [_make_chunk(""), _make_chunk("text"), _make_chunk("")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0
            ))
            time.sleep(0.5)
            worker.stop()

        partials = []
        while not ui_q.empty():
            msg = ui_q.get_nowait()
            if msg[0] == "partial":
                partials.append(msg[2])

        self.assertEqual(partials, ["text"])

    def test_valueerror_chunks_skipped(self):
        """Chunks raising ValueError on .text should be skipped."""
        chunks = [_make_chunk_valueerror(), _make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0
            ))
            time.sleep(0.5)
            worker.stop()

        partials = []
        while not ui_q.empty():
            msg = ui_q.get_nowait()
            if msg[0] == "partial":
                partials.append(msg[2])

        self.assertEqual(partials, ["ok"])

    def test_no_text_no_partial_start(self):
        """If all chunks are empty, no partial_start/end should be sent."""
        chunks = [_make_chunk(""), _make_chunk("")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("partial_start", types)
        self.assertNotIn("partial_end", types)

    def test_auto_stream_strips_direction_header_and_emits_metadata(self):
        chunks = [_make_chunk("DIRECTION: en_ja\nTRANSLATION: "), _make_chunk("こんにちは")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen_auto", phase=0,
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        partials = [m[2] for m in messages if m[0] == "partial"]
        self.assertEqual("".join(partials), "こんにちは")
        done = [m for m in messages if m[0] == "translation_done"][0]
        self.assertEqual(done[1], "listen")
        self.assertEqual(done[2], "listen_auto")
        self.assertEqual(done[3], "en_ja")
        self.assertEqual(done[6], "こんにちは")
        self.assertIsNone(done[7])

    def test_auto_stream_direction_parse_failure_emits_incomplete_result(self):
        chunks = [_make_chunk("DIRECTION: maybe\nTRANSLATION: ???")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen_auto", phase=0,
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


class TestPhase1STT(unittest.TestCase):
    """Phase 1: STT only (non-streaming), transcript message and Phase 2 auto-submission."""

    def test_phase1_produces_transcript_and_phase2(self):
        """Phase 1 should produce transcript message and auto-submit Phase 2."""
        client = MagicMock()
        # Phase 1: non-streaming response
        phase1_response = MagicMock()
        phase1_response.text = "Hello world"
        client.models.generate_content.return_value = phase1_response
        # Phase 2: streaming
        phase2_chunks = [_make_chunk("translated")]
        client.models.generate_content_stream.return_value = iter(phase2_chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt prompt",
                stream_id="listen", phase=1, context="ctx"
            ))
            time.sleep(1.0)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        types = [m[0] for m in messages]
        # Should have transcript from Phase 1
        self.assertIn("transcript", types)
        transcript_msg = [m for m in messages if m[0] == "transcript"][0]
        self.assertEqual(transcript_msg[1], "listen")  # stream_id
        self.assertEqual(transcript_msg[3], "Hello world")  # text

        # Phase 2 should have been auto-submitted and produced streaming output
        self.assertIn("partial_start", types)
        self.assertIn("partial", types)
        self.assertIn("partial_end", types)

    def test_phase1_uses_non_streaming(self):
        """Phase 1 should use generate_content (non-streaming), not generate_content_stream."""
        client = MagicMock()
        response = MagicMock()
        response.text = "transcript"
        client.models.generate_content.return_value = response
        # Phase 2 streaming
        client.models.generate_content_stream.return_value = iter([_make_chunk("ok")])
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt",
                stream_id="listen", phase=1, context="ctx"
            ))
            time.sleep(1.0)
            worker.stop()

        # Phase 1 used generate_content (non-streaming)
        client.models.generate_content.assert_called_once()

    def test_phase1_calls_build_translation_prompt(self):
        """Phase 1 should call build_translation_prompt for Phase 2."""
        client = MagicMock()
        response = MagicMock()
        response.text = "some text"
        client.models.generate_content.return_value = response
        phase2_chunks = [_make_chunk("translation")]
        client.models.generate_content_stream.return_value = iter(phase2_chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types, \
             patch("realtime_translator.api.build_translation_prompt", return_value="built_prompt") as mock_build:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt",
                stream_id="listen", phase=1, context="my context"
            ))
            time.sleep(1.0)
            worker.stop()

        mock_build.assert_called_once_with("listen", "my context", "some text")


class TestPhase2Translation(unittest.TestCase):
    """Phase 2: translation only (no wav_bytes)."""

    def test_phase2_no_audio_part(self):
        """Phase 2 should not create audio Part (wav_bytes is None)."""
        chunks = [_make_chunk("translated text")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=None, prompt="translate this",
                stream_id="listen", phase=2
            ))
            time.sleep(0.5)
            worker.stop()

        # genai_types.Part.from_bytes should NOT have been called
        mock_types.Part.from_bytes.assert_not_called()

        # Should still produce streaming output
        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertIn("partial_start", types)
        self.assertIn("partial", types)
        self.assertIn("partial_end", types)

    def test_phase2_contents_is_prompt_only(self):
        """Phase 2 contents should be [prompt] only, no audio part."""
        chunks = [_make_chunk("result")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types"):
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=None, prompt="my prompt",
                stream_id="listen", phase=2
            ))
            time.sleep(0.5)
            worker.stop()

        # Verify contents passed to generate_content_stream
        call_args = client.models.generate_content_stream.call_args
        self.assertEqual(call_args.kwargs.get("contents") or call_args[1].get("contents", call_args),
                         ["my prompt"])


class TestSilenceSentinel(unittest.TestCase):
    """SILENCE_SENTINEL filtering in Phase 1."""

    def test_silence_sentinel_no_transcript(self):
        """If transcript contains SILENCE_SENTINEL, no transcript message should be sent."""
        client = MagicMock()
        response = MagicMock()
        response.text = SILENCE_SENTINEL
        client.models.generate_content.return_value = response
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt",
                stream_id="listen", phase=1
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("transcript", types)
        self.assertNotIn("partial_start", types)

    def test_silence_sentinel_embedded_no_phase2(self):
        """If transcript contains SILENCE_SENTINEL (even embedded), no Phase 2 submission."""
        client = MagicMock()
        response = MagicMock()
        response.text = f"text {SILENCE_SENTINEL} more"
        client.models.generate_content.return_value = response
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt",
                stream_id="listen", phase=1
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("transcript", types)

    def test_empty_transcript_no_phase2(self):
        """If transcript is empty after strip, no Phase 2 submission."""
        client = MagicMock()
        response = MagicMock()
        response.text = "    "
        client.models.generate_content.return_value = response
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt",
                stream_id="listen", phase=1
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())
        types = [m[0] for m in messages]
        self.assertNotIn("transcript", types)


class TestErrorHandling(unittest.TestCase):
    """Error handling: API exception -> error message in UI queue."""

    def test_streaming_exception_produces_error(self):
        """Exception during streaming (Phase 0) should produce error message."""
        client = MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError("API failure")
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        error_msgs = [m for m in messages if m[0] == "error"]
        self.assertEqual(len(error_msgs), 1)
        self.assertEqual(error_msgs[0][1], "listen")
        self.assertIn("API failure", error_msgs[0][2])

    def test_phase1_exception_produces_error(self):
        """Exception during Phase 1 STT should produce error message."""
        client = MagicMock()
        client.models.generate_content_stream.side_effect = ConnectionError("network down")
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="speak", phase=1
            ))
            time.sleep(0.5)
            worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        error_msgs = [m for m in messages if m[0] == "error"]
        self.assertEqual(len(error_msgs), 1)
        self.assertEqual(error_msgs[0][1], "speak")
        self.assertIn("network down", error_msgs[0][2])

    def test_mid_stream_exception_sends_partial_end(self):
        """If exception occurs mid-stream (after partial_start), partial_end should still be sent."""
        def _failing_stream(*args, **kwargs):
            yield _make_chunk("text")
            raise RuntimeError("mid-stream failure")

        client = MagicMock()
        client.models.generate_content_stream.side_effect = _failing_stream
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0
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
        """Second API call should be delayed by min_interval_sec."""
        chunks = [_make_chunk("ok")]
        client = MagicMock()
        client.models.generate_content_stream.return_value = iter(chunks)
        ui_q = queue.Queue()

        interval = 0.3
        worker = ApiWorker(ui_q, client=client, min_interval_sec=interval)

        # Simulate a recent call (phase 0 uses audio timer)
        worker._last_audio_call_time = time.monotonic()

        with patch("realtime_translator.api.genai_types") as mock_types, \
             patch("realtime_translator.api.time.sleep") as mock_sleep:
            mock_types.Part.from_bytes.return_value = "audio_part"
            req = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0)
            worker._call_api(req)

        # sleep should have been called with approximately interval seconds
        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        self.assertGreater(sleep_arg, 0)
        self.assertLessEqual(sleep_arg, interval)

    def test_no_rate_limit_on_first_call(self):
        """First call (last_call_time=0) should not be rate-limited."""
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = ApiWorker(ui_q, client=client, min_interval_sec=5.0)

        with patch("realtime_translator.api.genai_types") as mock_types, \
             patch("realtime_translator.api.time.sleep") as mock_sleep:
            mock_types.Part.from_bytes.return_value = "audio_part"
            req = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0)
            worker._call_api(req)

        # Should not sleep since elapsed >> min_interval
        mock_sleep.assert_not_called()

    def test_audio_call_time_updated_after_call(self):
        """_last_audio_call_time should be updated after phase 0 call."""
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
        before = time.monotonic()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            req = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0)
            worker._call_api(req)

        self.assertGreaterEqual(worker._last_audio_call_time, before)

    def test_text_call_time_updated_after_phase2(self):
        """_last_text_call_time should be updated after phase 2 call."""
        chunks = [_make_chunk("ok")]
        client = _mock_client(chunks)
        ui_q = queue.Queue()

        worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
        before = time.monotonic()

        with patch("realtime_translator.api.genai_types") as mock_types:
            req = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
            worker._call_api(req)

        self.assertGreaterEqual(worker._last_text_call_time, before)

    def test_phase1_then_phase2_no_rate_limit_delay(self):
        """Phase 1 (audio) then Phase 2 (text) should use separate rate limit timers."""
        client = MagicMock()
        response = MagicMock()
        response.text = "transcript"
        client.models.generate_content.return_value = response
        client.models.generate_content_stream.return_value = iter([_make_chunk("ok")])
        ui_q = queue.Queue()

        worker = ApiWorker(ui_q, client=client, min_interval_sec=10.0)

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            # Phase 1 call
            req1 = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=1, context="ctx")
            worker._call_api(req1)
            # Phase 2 should NOT be rate-limited by phase 1's timer
            req2 = ApiRequest(wav_bytes=None, prompt="p", stream_id="listen", phase=2)
            t0 = time.monotonic()
            worker._call_api(req2)
            elapsed = time.monotonic() - t0
            # Should complete quickly, not waiting 10 seconds
            self.assertLess(elapsed, 2.0)

    def test_last_call_time_updated_even_on_error(self):
        """Rate limit time should be updated even when API call fails."""
        client = MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError("fail")
        ui_q = queue.Queue()

        worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
        before = time.monotonic()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            req = ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0)
            worker._call_api(req)

        self.assertGreaterEqual(worker._last_audio_call_time, before)


class TestStopSentinel(unittest.TestCase):
    """Stop sentinel (None) causes worker loop to exit."""

    def test_none_in_queue_exits_loop(self):
        """Putting None directly in the request queue should exit the worker loop."""
        ui_q = queue.Queue()
        client = MagicMock()
        worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
        worker._running = True
        worker._req_queue.put(None)

        # Run loop in a thread with timeout
        done = threading.Event()

        def run_loop():
            worker._worker_loop()
            done.set()

        t = threading.Thread(target=run_loop)
        t.start()
        finished = done.wait(timeout=3.0)
        t.join(timeout=1.0)
        self.assertTrue(finished, "Worker loop should have exited on None sentinel")


class TestLabelParameter(unittest.TestCase):
    """Label parameter for thread naming."""

    def test_custom_label(self):
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0, label="TestLabel")
        worker.start()
        self.assertEqual(worker._thread.name, "TestLabel")
        worker.stop()

    def test_default_label(self):
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker.start()
        self.assertEqual(worker._thread.name, "ApiWorker")
        worker.stop()


class TestPendingRequests(unittest.TestCase):
    """pending_requests counter accuracy tests."""

    def test_pending_requests_decrements_on_drop(self):
        """Fill queue, submit extra → pending_requests == maxsize (not maxsize+1)."""
        ui_q = queue.Queue()
        worker = ApiWorker(ui_q, client=MagicMock(), min_interval_sec=0)
        worker._running = True
        for i in range(API_QUEUE_MAXSIZE):
            worker.submit(ApiRequest(wav_bytes=b"x", prompt=f"p{i}", stream_id="listen"))
        # Submit one more — should drop oldest and decrement
        worker.submit(ApiRequest(wav_bytes=b"x", prompt="extra", stream_id="listen"))
        self.assertEqual(worker._pending_requests, API_QUEUE_MAXSIZE)

    def test_pending_requests_reaches_zero_after_processing(self):
        """Submit items, let worker process → pending_requests == 0."""
        client = _mock_client([_make_chunk("ok")])
        ui_q = queue.Queue()
        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            worker.submit(ApiRequest(wav_bytes=b"wav", prompt="p", stream_id="listen", phase=0))
            time.sleep(0.5)
            worker.stop()
        self.assertEqual(worker._pending_requests, 0)

class TestPhase1SelfSubmitFullQueue(unittest.TestCase):
    """Phase 1 self-submit with full queue: pending_requests stays correct."""

    def test_phase1_self_submit_with_full_queue(self):
        client = MagicMock()
        phase1_response = MagicMock()
        phase1_response.text = "Hello world"
        client.models.generate_content.return_value = phase1_response
        phase2_chunks = [_make_chunk("translated")]
        client.models.generate_content_stream.return_value = iter(phase2_chunks)
        ui_q = queue.Queue()

        with patch("realtime_translator.api.genai_types") as mock_types:
            mock_types.Part.from_bytes.return_value = "audio_part"
            worker = ApiWorker(ui_q, client=client, min_interval_sec=0)
            worker.start()
            # Fill the queue first
            for i in range(API_QUEUE_MAXSIZE - 1):
                worker.submit(ApiRequest(wav_bytes=b"x", prompt=f"filler{i}", stream_id="listen"))
            # Submit phase 1 — it will self-submit phase 2, potentially when queue is full
            worker.submit(ApiRequest(
                wav_bytes=b"wav", prompt="stt", stream_id="listen", phase=1, context="ctx"
            ))
            time.sleep(2.0)
            worker.stop()

        # pending_requests should be 0 after all processing
        self.assertEqual(worker._pending_requests, 0)


if __name__ == "__main__":
    unittest.main()
