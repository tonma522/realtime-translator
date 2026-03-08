"""WhisperTranscriber / WhisperWorker テスト"""
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from realtime_translator import whisper_stt as ws_module
from realtime_translator.whisper_stt import WhisperTranscriber, WhisperWorker
from realtime_translator.api import ApiRequest, ApiWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_segments(*texts):
    """Create mock segment objects with .text attribute."""
    return [SimpleNamespace(text=t) for t in texts]


def _make_mock_model(segments=None):
    """Return a mock WhisperModel whose transcribe() returns (segments, info)."""
    mock = MagicMock()
    if segments is None:
        segments = []
    mock.transcribe.return_value = (segments, SimpleNamespace(language="en"))
    return mock


# ---------------------------------------------------------------------------
# WhisperTranscriber
# ---------------------------------------------------------------------------

class TestWhisperTranscriber:
    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_transcribe_joins_segments(self, MockModel):
        segments = _make_segments("Hello", "world")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        t = WhisperTranscriber(model_size="small", language="en")
        result = t.transcribe(b"\x00\x01")
        assert result == "Hello world"
        mock_model.transcribe.assert_called_once()

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_transcribe_empty_segments(self, MockModel):
        mock_model = _make_mock_model([])
        MockModel.return_value = mock_model

        t = WhisperTranscriber()
        result = t.transcribe(b"\x00")
        assert result == ""

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_transcribe_strips_whitespace(self, MockModel):
        segments = _make_segments("  Hello  ", "  ", "  world  ")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        t = WhisperTranscriber()
        result = t.transcribe(b"\x00")
        # " ".join strips each segment, so empty-after-strip produces empty string
        assert result == "Hello  world"

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_transcribe_whitespace_only_segments(self, MockModel):
        segments = _make_segments("   ", "\t", "\n")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        t = WhisperTranscriber()
        result = t.transcribe(b"\x00")
        # All segments strip to empty string, joined with spaces
        assert result.strip() == ""

    @patch.object(ws_module, "WHISPER_AVAILABLE", False)
    def test_unavailable_raises(self):
        with pytest.raises(RuntimeError, match="faster-whisper"):
            WhisperTranscriber()


# ---------------------------------------------------------------------------
# WhisperWorker
# ---------------------------------------------------------------------------

def _build_worker(transcribe_return="Hello", model_init_ok=True):
    """Build a WhisperWorker with mocked internals."""
    ui_queue = queue.Queue()
    mock_api_listen = MagicMock(spec=ApiWorker)
    mock_api_listen.is_running = True
    mock_api_speak = MagicMock(spec=ApiWorker)
    mock_api_speak.is_running = True

    worker = WhisperWorker(
        api_worker_listen=mock_api_listen,
        api_worker_speak=mock_api_speak,
        ui_queue=ui_queue,
        model_size="small",
        language="en",
        context="test context",
    )
    return worker, ui_queue, mock_api_listen, mock_api_speak


class TestWhisperWorkerLifecycle:
    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_start_stop(self, MockModel):
        MockModel.return_value = _make_mock_model()
        worker, ui_queue, _, _ = _build_worker()

        worker.start()
        assert worker._running is True
        assert worker._thread is not None
        assert worker._thread.is_alive()

        worker.stop()
        assert worker._running is False
        assert worker._thread is None

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_start_emits_status_messages(self, MockModel):
        MockModel.return_value = _make_mock_model()
        worker, ui_queue, _, _ = _build_worker()

        worker.start()
        # Give the thread time to initialize
        time.sleep(0.3)
        worker.stop()

        messages = []
        while not ui_queue.empty():
            messages.append(ui_queue.get_nowait())

        statuses = [m for m in messages if m[0] == "status"]
        assert any("準備中" in s[1] for s in statuses)
        assert any("準備完了" in s[1] for s in statuses)

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_model_init_failure_emits_error(self, MockModel):
        MockModel.side_effect = RuntimeError("GPU OOM")
        worker, ui_queue, _, _ = _build_worker()

        worker.start()
        # Wait for thread to finish (it should exit after error)
        time.sleep(0.5)

        messages = []
        while not ui_queue.empty():
            messages.append(ui_queue.get_nowait())

        errors = [m for m in messages if m[0] == "error"]
        assert len(errors) >= 1
        assert "GPU OOM" in errors[0][2]
        assert worker._running is False


class TestWhisperWorkerSubmit:
    def test_submit_when_not_running_is_noop(self):
        worker, _, _, _ = _build_worker()
        # Not started, _running is False
        worker.submit(b"\x00", "listen")
        assert worker._req_queue.empty()

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_submit_enqueues_wav(self, MockModel):
        MockModel.return_value = _make_mock_model()
        worker, _, _, _ = _build_worker()
        worker._running = True  # Simulate started state without thread

        worker.submit(b"\x00\x01", "listen")
        assert not worker._req_queue.empty()
        item = worker._req_queue.get_nowait()
        assert item == (b"\x00\x01", "listen")

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_queue_overflow_drops_oldest(self, MockModel):
        MockModel.return_value = _make_mock_model()
        worker, _, _, _ = _build_worker()
        worker._running = True

        # Fill queue to maxsize (3)
        worker.submit(b"chunk1", "listen")
        worker.submit(b"chunk2", "listen")
        worker.submit(b"chunk3", "listen")

        # Submit one more -- should drop oldest
        worker.submit(b"chunk4", "listen")

        items = []
        while not worker._req_queue.empty():
            items.append(worker._req_queue.get_nowait())

        wav_bytes_list = [i[0] for i in items]
        assert b"chunk1" not in wav_bytes_list
        assert b"chunk4" in wav_bytes_list


class TestWhisperWorkerPipeline:
    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_transcription_submits_phase2_to_api_worker(self, MockModel):
        segments = _make_segments("Hello world")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        worker, ui_queue, mock_api_listen, _ = _build_worker()
        worker.start()
        time.sleep(0.3)  # Let model init

        worker.submit(b"\x00\x01", "listen")
        time.sleep(0.5)  # Let processing happen
        worker.stop()

        # Check that api_worker_listen.submit was called with Phase 2 request
        assert mock_api_listen.submit.called
        call_args = mock_api_listen.submit.call_args[0][0]
        assert isinstance(call_args, ApiRequest)
        assert call_args.phase == 2
        assert call_args.wav_bytes is None
        assert call_args.transcript == "Hello world"

        # Check transcript was emitted to UI queue
        messages = []
        while not ui_queue.empty():
            messages.append(ui_queue.get_nowait())
        transcripts = [m for m in messages if m[0] == "transcript"]
        assert len(transcripts) >= 1
        assert transcripts[0][1] == "listen"
        assert transcripts[0][3] == "Hello world"

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_empty_transcript_does_not_submit(self, MockModel):
        segments = _make_segments("")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        worker, ui_queue, mock_api_listen, _ = _build_worker()
        worker.start()
        time.sleep(0.3)

        worker.submit(b"\x00", "listen")
        time.sleep(0.5)
        worker.stop()

        mock_api_listen.submit.assert_not_called()

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_speak_stream_uses_speak_api_worker(self, MockModel):
        segments = _make_segments("Konnichiwa")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        worker, ui_queue, mock_api_listen, mock_api_speak = _build_worker()
        worker.start()
        time.sleep(0.3)

        worker.submit(b"\x00\x01", "speak")
        time.sleep(0.5)
        worker.stop()

        # speak stream should use api_worker_speak
        assert mock_api_speak.submit.called
        mock_api_listen.submit.assert_not_called()

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_transcribe_error_emits_error_to_ui(self, MockModel):
        mock_model = _make_mock_model()
        mock_model.transcribe.side_effect = RuntimeError("decode error")
        MockModel.return_value = mock_model

        worker, ui_queue, _, _ = _build_worker()
        worker.start()
        time.sleep(0.3)

        worker.submit(b"\x00", "listen")
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_queue.empty():
            messages.append(ui_queue.get_nowait())

        errors = [m for m in messages if m[0] == "error"]
        assert len(errors) >= 1
        assert "listen" in errors[0][1]
        assert "decode error" in errors[0][2]

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_api_worker_not_running_skips_submit(self, MockModel):
        segments = _make_segments("Hello")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model

        worker, ui_queue, mock_api_listen, _ = _build_worker()
        mock_api_listen.is_running = False  # API worker stopped

        worker.start()
        time.sleep(0.3)

        worker.submit(b"\x00", "listen")
        time.sleep(0.5)
        worker.stop()

        # Transcript should still be emitted, but no Phase 2 submission
        mock_api_listen.submit.assert_not_called()


class TestPendingRequests:
    def test_pending_requests_decrements_on_drop(self):
        worker, _, _, _ = _build_worker()
        worker._running = True
        worker.submit(b"a", "listen")
        worker.submit(b"b", "listen")
        worker.submit(b"c", "listen")  # queue full (maxsize=3)
        worker.submit(b"d", "listen")  # drops oldest
        assert worker._pending_requests == 3  # not 4

    @patch.object(ws_module, "WHISPER_AVAILABLE", True)
    @patch.object(ws_module, "WhisperModel")
    def test_pending_requests_reaches_zero_after_processing(self, MockModel):
        segments = _make_segments("Hello")
        mock_model = _make_mock_model(segments)
        MockModel.return_value = mock_model
        worker, _, _, _ = _build_worker()
        worker.start()
        time.sleep(0.3)
        worker.submit(b"\x00", "listen")
        time.sleep(0.5)
        worker.stop()
        assert worker._pending_requests == 0

