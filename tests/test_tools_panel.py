"""ToolsPanel 結線テスト"""
import tkinter as tk
from unittest.mock import MagicMock, patch
import pytest
from realtime_translator.tools_panel import ToolsPanel
from realtime_translator.history import HistoryEntry


@pytest.fixture
def root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def panel(root):
    ctrl = MagicMock()
    ctrl.history.all_entries.return_value = []
    ctrl.history.get_by_seq.return_value = None
    ctrl.can_retranslate.return_value = True
    ctrl.can_assist.return_value = True
    return ToolsPanel(root, ctrl)


class TestUpdateLatestEntry:
    def test_latest_label_updates(self, panel):
        entry = HistoryEntry(seq=1, stream_id="listen", timestamp="12:00:00",
                             original="Hello world", translation="こんにちは世界")
        panel.update_latest_entry(entry)
        assert "#1" in panel._latest_label.cget("text")
        assert "PC音声" in panel._latest_label.cget("text")

    def test_auto_selects_last(self, panel):
        for i in range(3):
            entry = HistoryEntry(seq=i+1, stream_id="listen", timestamp=f"12:0{i}:00",
                                 original=f"text {i}", translation=f"訳 {i}")
            panel.update_latest_entry(entry)
        sel = panel._retrans_listbox.curselection()
        assert sel == (2,)


class TestReset:
    def test_clears_pending_and_list(self, panel):
        panel._pending_retrans_id = "batch-123"
        panel._pending_assist_id = "req-456"
        entry = HistoryEntry(seq=1, stream_id="listen", timestamp="12:00:00",
                             original="test", translation="テスト")
        panel.update_latest_entry(entry)
        panel.reset()
        assert panel._pending_retrans_id is None
        assert panel._pending_assist_id is None
        assert panel._retrans_listbox.size() == 0
        assert "—" in panel._latest_label.cget("text")


class TestRetransResult:
    def test_ignores_stale_batch(self, panel):
        panel._pending_retrans_id = "batch-A"
        panel.on_retrans_result("batch-B", 1, "stale result")
        # Result text should still be empty
        txt = panel._retrans_result_text.get("1.0", "end-1c")
        assert txt == ""

    def test_processes_matching_batch(self, panel):
        panel._pending_retrans_id = "batch-A"
        entry = HistoryEntry(seq=1, stream_id="listen", timestamp="12:00:00",
                             original="Hello", translation="こんにちは")
        panel._controller.history.get_by_seq.return_value = entry
        panel.on_retrans_result("batch-A", 1, "再翻訳結果")
        txt = panel._retrans_result_text.get("1.0", "end-1c")
        assert "再翻訳結果" in txt
        assert panel._pending_retrans_id is None


class TestSyncToolStates:
    def test_set_button_states(self, panel):
        panel.set_button_states(retranslate_enabled=False, assist_enabled=True, minutes_enabled=False)
        assert str(panel._retrans_btn.cget("state")) == "disabled"
        assert str(panel._assist_btn.cget("state")) == "normal"
        assert str(panel._minutes_btn.cget("state")) == "disabled"


class TestSessionIsolation:
    def test_reset_prevents_stale_results(self, panel):
        panel._pending_retrans_id = "old-batch"
        panel.reset()
        panel.on_retrans_result("old-batch", 1, "stale")
        txt = panel._retrans_result_text.get("1.0", "end-1c")
        assert txt == ""
