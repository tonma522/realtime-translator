"""TranslationTimelinePanel tests."""

import os
import sys
import tkinter as tk

import pytest

from realtime_translator.translation_timeline_panel import TranslationTimelinePanel


@pytest.fixture(scope="module")
def root():
    python_dir = os.path.dirname(sys.executable)
    os.environ["TCL_LIBRARY"] = os.path.join(python_dir, "tcl", "tcl8.6")
    os.environ["TK_LIBRARY"] = os.path.join(python_dir, "tcl", "tk8.6")
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_partial_sequence_appends_header_and_text(root):
    panel = TranslationTimelinePanel(root)

    panel.on_partial_start("listen_auto", "12:00:00")
    panel.on_partial("listen_auto", "hello")

    dumped = panel.dump_text()
    assert "12:00:00" in dumped
    assert "hello" in dumped


def test_runtime_error_renders_without_overwriting_status(root):
    panel = TranslationTimelinePanel(root)

    panel.set_global_status("running", "翻訳中")
    panel.on_runtime_error("listen", "API limit exceeded")

    assert panel.status_text() == "状態: 翻訳中"
    assert "API limit exceeded" in panel.dump_text()
