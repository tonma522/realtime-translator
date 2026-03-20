"""MainControlsPanel tests."""

import os
import sys

_PYTHON_DIR = os.path.dirname(sys.executable)
os.environ["TCL_LIBRARY"] = os.path.join(_PYTHON_DIR, "tcl", "tcl8.6")
os.environ["TK_LIBRARY"] = os.path.join(_PYTHON_DIR, "tcl", "tk8.6")

from realtime_translator.main_controls_panel import MainControlsPanel


def _make_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    return root


def test_session_summary_shows_direction_and_mode():
    root = _make_root()
    try:
        panel = MainControlsPanel(
            root,
            on_toggle=lambda: None,
            on_open_settings=lambda: None,
            on_reload_devices=lambda: None,
            on_clear=lambda: None,
            on_export=lambda: None,
        )

        panel.apply_session_summary(
            listen_enabled=True,
            speak_enabled=True,
            pc_audio_label="PC音声: 英語→日本語",
            mic_label="マイク: 日本語→英語",
            mode_summary=["録音モード: PTT", "翻訳方式: 通常"],
            device_summary=("PC音声デバイス: Speakers (Loopback)", "マイクデバイス: Yeti Nano"),
            backend_summary="STT: Gemini (内蔵) / 翻訳: Gemini",
            config_updated_at="15:42:18",
        )

        dumped = panel.dump_labels()
        assert "PC音声: 英語→日本語" in dumped
        assert "マイク: 日本語→英語" in dumped
        assert "録音モード: PTT" in dumped
        assert "PC音声デバイス: Speakers (Loopback)" in dumped
        assert "マイクデバイス: Yeti Nano" in dumped
        assert "STT: Gemini (内蔵) / 翻訳: Gemini" in dumped
        assert "構成更新: 15:42:18" in dumped
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


def test_quick_actions_support_state_and_helper_text():
    root = _make_root()
    try:
        panel = MainControlsPanel(
            root,
            on_toggle=lambda: None,
            on_open_settings=lambda: None,
            on_reload_devices=lambda: None,
            on_clear=lambda: None,
            on_export=lambda: None,
        )

        panel.set_quick_action_state(
            reload_enabled=False,
            clear_enabled=False,
            export_enabled=True,
            helper_text="初期化中",
        )

        assert panel.quick_action_labels() == ("デバイス再読込", "結果クリア", "エクスポート")
        assert panel.quick_action_states() == {
            "reload": "disabled",
            "clear": "disabled",
            "export": "normal",
        }
        assert panel.quick_action_helper_text() == "初期化中"
    finally:
        root.destroy()


def test_quick_actions_reflow_between_two_columns_and_single_column():
    root = _make_root()
    try:
        panel = MainControlsPanel(
            root,
            on_toggle=lambda: None,
            on_open_settings=lambda: None,
            on_reload_devices=lambda: None,
            on_clear=lambda: None,
            on_export=lambda: None,
        )

        panel._layout_quick_actions(420)
        assert panel.quick_action_grid_positions() == {
            "reload": (0, 0),
            "clear": (0, 1),
            "export": (1, 0),
        }

        panel._layout_quick_actions(320)
        assert panel.quick_action_grid_positions() == {
            "reload": (0, 0),
            "clear": (1, 0),
            "export": (2, 0),
        }
    finally:
        root.destroy()
