"""MainControlsPanel tests."""

import os
import sys
import tkinter as tk

from realtime_translator.main_controls_panel import MainControlsPanel


def _make_root():
    python_dir = os.path.dirname(sys.executable)
    os.environ["TCL_LIBRARY"] = os.path.join(python_dir, "tcl", "tcl8.6")
    os.environ["TK_LIBRARY"] = os.path.join(python_dir, "tcl", "tk8.6")
    root = tk.Tk()
    root.withdraw()
    return root


def test_session_summary_shows_direction_and_mode():
    root = _make_root()
    try:
        panel = MainControlsPanel(root, on_toggle=lambda: None, on_open_settings=lambda: None)

        panel.apply_session_summary(
            listen_enabled=True,
            speak_enabled=True,
            pc_audio_label="PC音声: 英語→日本語",
            mic_label="マイク: 日本語→英語",
            mode_summary=["録音モード: PTT", "翻訳方式: 通常"],
        )

        dumped = panel.dump_labels()
        assert "PC音声: 英語→日本語" in dumped
        assert "マイク: 日本語→英語" in dumped
        assert "録音モード: PTT" in dumped
    finally:
        root.destroy()


def test_blocker_card_visible_when_message_present():
    root = _make_root()
    try:
        panel = MainControlsPanel(root, on_toggle=lambda: None, on_open_settings=lambda: None)

        panel.set_blocker("APIキーが未設定")

        assert panel.blocker_visible() is True
    finally:
        root.destroy()
