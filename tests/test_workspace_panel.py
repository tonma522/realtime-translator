"""WorkspacePanel notebook tests."""

import os
import sys
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from realtime_translator.history import HistoryEntry
from realtime_translator.workspace_panel import WorkspacePanel


@pytest.fixture(scope="module")
def root():
    python_dir = os.path.dirname(sys.executable)
    os.environ["TCL_LIBRARY"] = os.path.join(python_dir, "tcl", "tcl8.6")
    os.environ["TK_LIBRARY"] = os.path.join(python_dir, "tcl", "tk8.6")
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def controller():
    ctrl = MagicMock()
    ctrl.history.all_entries.return_value = []
    ctrl.history.get_by_seq.return_value = None
    return ctrl


def test_workspace_has_three_tabs(root, controller):
    panel = WorkspacePanel(root, controller)

    assert panel.tab_labels() == ["再翻訳", "返答アシスト", "議事録"]


def test_history_update_does_not_switch_active_tab(root, controller):
    panel = WorkspacePanel(root, controller)
    panel.select_tab("議事録")

    entry = HistoryEntry(
        seq=1,
        stream_id="listen",
        timestamp="12:00:00",
        original="hello",
        translation="こんにちは",
    )
    panel.on_history_entry(entry)

    assert panel.active_tab_label() == "議事録"
