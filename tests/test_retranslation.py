"""Tests for RetranslationWorker"""
import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from realtime_translator.history import TranslationHistory
from realtime_translator.retranslation import RetranslationWorker


class FakeWorker:
    """Fake worker with pending_requests and is_busy."""
    def __init__(self):
        self._pending = 0
        self._busy = False

    @property
    def pending_requests(self):
        return self._pending

    @property
    def is_busy(self):
        return self._busy


class TestRetranslationWorker:
    def _make_history(self):
        h = TranslationHistory()
        h.append("listen", "12:00:00", "Hello", "こんにちは")
        h.append("speak", "12:00:05", "はい", "Yes")
        h.append("listen", "12:00:10", "The lead time is 3 weeks", "リードタイムは3週間です")
        return h

    def test_submit_returns_batch_id(self):
        ui_q = queue.Queue()
        h = self._make_history()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="test", api_key="key",
            min_interval_sec=0,
        )
        batch_id = worker.submit(1, 2, "context")
        assert len(batch_id) == 8  # hex[:8]
        worker.stop()

    def test_idle_check_blocks_when_busy(self):
        ui_q = queue.Queue()
        h = self._make_history()
        fake_w = FakeWorker()
        fake_w._busy = True

        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[fake_w],
            llm_backend="gemini", model="test", api_key="key",
            min_interval_sec=0,
        )
        assert not worker._all_workers_idle()
        fake_w._busy = False
        assert worker._all_workers_idle()

    def test_idle_check_pending(self):
        fake_w = FakeWorker()
        fake_w._pending = 1
        ui_q = queue.Queue()
        h = self._make_history()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[fake_w],
            llm_backend="gemini", model="test", api_key="key",
        )
        assert not worker._all_workers_idle()
        fake_w._pending = 0
        assert worker._all_workers_idle()

    def test_execute_gemini(self):
        ui_q = queue.Queue()
        h = self._make_history()

        mock_client = MagicMock()
        response = MagicMock()
        response.text = "再翻訳結果"
        mock_client.models.generate_content.return_value = response

        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="gemini-2.0-flash", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: mock_client,
        )
        worker.start()
        batch_id = worker.submit(2, 1, "会議")
        time.sleep(1.0)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        results = [m for m in messages if m[0] == "retrans_result"]
        assert len(results) == 1
        assert results[0][1] == batch_id
        assert results[0][2] == 2
        assert results[0][3] == "再翻訳結果"

    def test_execute_openai(self):
        ui_q = queue.Queue()
        h = self._make_history()

        mock_client = MagicMock()
        response = MagicMock()
        message = MagicMock()
        message.content = "retranslated"
        choice = MagicMock()
        choice.message = message
        response.choices = [choice]
        mock_client.chat.completions.create.return_value = response

        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="openai", model="gpt-4o", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: mock_client,
        )
        worker.start()
        batch_id = worker.submit(1, 1, "ctx")
        time.sleep(1.0)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        results = [m for m in messages if m[0] == "retrans_result"]
        assert len(results) == 1
        assert results[0][3] == "retranslated"

    def test_retranslation_result_is_annotated_once(self):
        ui_q = queue.Queue()
        h = self._make_history()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="gemini-2.0-flash", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: MagicMock(),
        )
        req = type("Req", (), {"center_seq": 1, "n_surrounding": 1, "context": "ctx", "batch_id": "batch"})()

        with patch.object(worker, "_call_gemini", return_value="35 psi") as call_gemini, \
             patch("realtime_translator.retranslation.annotate_translation", return_value="annotated") as annotate:
            result = worker._execute_retranslation(req, MagicMock())

        assert result == "annotated"
        call_gemini.assert_called_once()
        annotate.assert_called_once_with("35 psi", output_language="ja")

    def test_retranslation_annotation_failure_falls_back_to_raw(self):
        ui_q = queue.Queue()
        h = self._make_history()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="gemini-2.0-flash", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: MagicMock(),
        )
        req = type("Req", (), {"center_seq": 1, "n_surrounding": 1, "context": "ctx", "batch_id": "batch"})()

        with patch.object(worker, "_call_gemini", return_value="35 psi"), \
             patch("realtime_translator.retranslation.annotate_translation", side_effect=RuntimeError("boom")):
            result = worker._execute_retranslation(req, MagicMock())

        assert result == "35 psi"

    def test_retranslation_auto_stream_resolves_output_language_from_source_default(self):
        ui_q = queue.Queue()
        h = TranslationHistory()
        h.append(
            "listen",
            "12:00:00",
            "Hello",
            "こんにちは",
            virtual_stream_id="listen_auto",
            resolved_direction=None,
        )
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="gemini-2.0-flash", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: MagicMock(),
        )
        req = type("Req", (), {"center_seq": 1, "n_surrounding": 0, "context": "ctx", "batch_id": "batch"})()

        with patch.object(worker, "_call_gemini", return_value="12 mm"), \
             patch("realtime_translator.retranslation.annotate_translation", return_value="annotated") as annotate:
            result = worker._execute_retranslation(req, MagicMock())

        assert result == "annotated"
        annotate.assert_called_once_with("12 mm", output_language="ja")

    def test_missing_entry_error(self):
        ui_q = queue.Queue()
        h = TranslationHistory()

        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="test", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: MagicMock(),
        )
        worker.start()
        batch_id = worker.submit(999, 1, "ctx")
        time.sleep(0.5)
        worker.stop()

        messages = []
        while not ui_q.empty():
            messages.append(ui_q.get_nowait())

        errors = [m for m in messages if m[0] == "retrans_error"]
        assert len(errors) == 1
        assert "999" in errors[0][2]

    def test_direction_parse_failed_entry_is_rejected(self):
        ui_q = queue.Queue()
        h = TranslationHistory()
        h.append(
            "listen",
            "12:00:00",
            "Hello",
            "",
            error="direction_parse_failed",
        )

        mock_client = MagicMock()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="test", api_key="key",
            min_interval_sec=0,
            client_factory=lambda: mock_client,
        )

        with pytest.raises(ValueError, match="direction_parse_failed"):
            worker._execute_retranslation(
                type("Req", (), {"center_seq": 1, "n_surrounding": 1, "context": "ctx", "batch_id": "batch"})(),
                mock_client,
            )
        assert not mock_client.models.generate_content.called

    def test_stop_lifecycle(self):
        ui_q = queue.Queue()
        h = self._make_history()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="test", api_key="key",
        )
        worker.start()
        worker.stop()
        assert worker._thread is None

    def test_signal_stop_with_full_queue(self):
        """signal_stop delivers sentinel even when queue is full."""
        ui_q = queue.Queue()
        h = self._make_history()
        worker = RetranslationWorker(
            ui_queue=ui_q, history=h, workers=[],
            llm_backend="gemini", model="test", api_key="key",
            min_interval_sec=0,
        )
        worker.start()
        # Fill queue (maxsize=20)
        for _ in range(20):
            worker.submit(1, 1, "ctx")
        # signal_stop should succeed even with full queue
        worker.signal_stop()
        worker.join(timeout=5)
        assert worker._thread is None
