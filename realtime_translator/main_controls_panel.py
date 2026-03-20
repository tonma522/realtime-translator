"""Main controls panel for the primary session actions."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class MainControlsPanel:
    def __init__(
        self,
        parent,
        *,
        on_toggle,
        on_open_settings,
        on_reload_devices=None,
        on_clear=None,
        on_export=None,
        enable_listen_var: tk.BooleanVar | None = None,
        enable_speak_var: tk.BooleanVar | None = None,
    ) -> None:
        self.frame = ttk.LabelFrame(parent, text="セッション")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(4, weight=1)

        self._enable_listen_var = enable_listen_var or tk.BooleanVar(value=True)
        self._enable_speak_var = enable_speak_var or tk.BooleanVar(value=True)

        header = ttk.Frame(self.frame)
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self._start_button = ttk.Button(header, text="▶ 翻訳開始", command=on_toggle)
        self._start_button.pack(side="left")

        self._settings_button = ttk.Button(header, text="詳細設定", command=on_open_settings)
        self._settings_button.pack(side="left", padx=(6, 0))

        self._ptt_container = ttk.Frame(header)

        stream_row = ttk.Frame(self.frame)
        stream_row.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        ttk.Checkbutton(stream_row, text="聴く (PC音声)", variable=self._enable_listen_var).pack(side="left")
        ttk.Checkbutton(stream_row, text="話す (マイク)", variable=self._enable_speak_var).pack(side="left", padx=(12, 0))

        summary_frame = ttk.Frame(self.frame)
        summary_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        summary_frame.columnconfigure(0, weight=1)

        self._streams_label = ttk.Label(summary_frame, text="有効: 聴く / 話す", anchor="w")
        self._streams_label.grid(row=0, column=0, sticky="ew")
        self._mode_labels = [
            ttk.Label(summary_frame, text="録音モード: 通常", anchor="w"),
            ttk.Label(summary_frame, text="翻訳方式: 通常", anchor="w"),
            ttk.Label(summary_frame, text="原文表示: ON", anchor="w"),
        ]
        for index, label in enumerate(self._mode_labels, start=1):
            label.grid(row=index, column=0, sticky="ew", pady=(2, 0))

        config_frame = ttk.LabelFrame(self.frame, text="セッション構成")
        config_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        config_frame.columnconfigure(0, weight=1)
        self._config_labels = [
            ttk.Label(config_frame, text="PC音声: 英語→日本語", anchor="w"),
            ttk.Label(config_frame, text="マイク: 日本語→英語", anchor="w"),
            ttk.Label(config_frame, text="PC音声デバイス: 未取得", anchor="w"),
            ttk.Label(config_frame, text="マイクデバイス: 未取得", anchor="w"),
            ttk.Label(config_frame, text="STT: 未設定 / 翻訳: 未設定", anchor="w"),
            ttk.Label(config_frame, text="構成更新: 未更新", anchor="w"),
        ]
        for index, label in enumerate(self._config_labels):
            label.grid(row=index, column=0, sticky="ew", padx=8, pady=(4 if index == 0 else 2, 0))

        quick_actions_frame = ttk.LabelFrame(self.frame, text="クイック操作")
        quick_actions_frame.grid(row=4, column=0, sticky="sew", padx=8, pady=(0, 8))
        quick_actions_frame.columnconfigure(0, weight=1)
        self._quick_actions_buttons_frame = ttk.Frame(quick_actions_frame)
        self._quick_actions_buttons_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        self._quick_actions_buttons_frame.columnconfigure(0, weight=1)
        self._quick_actions_buttons_frame.columnconfigure(1, weight=1)

        self._quick_action_buttons: dict[str, ttk.Button] = {}
        if on_reload_devices is not None:
            self._quick_action_buttons["reload"] = ttk.Button(
                self._quick_actions_buttons_frame,
                text="デバイス再読込",
                command=on_reload_devices,
            )
        if on_clear is not None:
            self._quick_action_buttons["clear"] = ttk.Button(
                self._quick_actions_buttons_frame,
                text="結果クリア",
                command=on_clear,
            )
        if on_export is not None:
            self._quick_action_buttons["export"] = ttk.Button(
                self._quick_actions_buttons_frame,
                text="エクスポート",
                command=on_export,
            )

        self._quick_action_order = ("reload", "clear", "export")
        self._quick_action_helper = ttk.Label(
            quick_actions_frame,
            text="",
            anchor="w",
            wraplength=320,
        )
        self._quick_action_helper.grid(row=1, column=0, sticky="ew", padx=8, pady=(6, 8))
        self._layout_quick_actions(420)
        self.frame.bind("<Configure>", self._on_frame_configure)

        self._blocker_frame = ttk.Frame(self.frame)
        self._blocker_label = ttk.Label(self._blocker_frame, foreground="#B71C1C", anchor="w", wraplength=320)
        self._blocker_label.pack(fill="x")

    @property
    def start_button(self):
        return self._start_button

    @property
    def settings_button(self):
        return self._settings_button

    @property
    def ptt_container(self):
        return self._ptt_container

    def set_toggle_button_text(self, text: str) -> None:
        self._start_button.configure(text=text)

    def apply_session_summary(
        self,
        *,
        listen_enabled: bool,
        speak_enabled: bool,
        pc_audio_label: str,
        mic_label: str,
        mode_summary: list[str] | tuple[str, ...],
        device_summary: tuple[str, str],
        backend_summary: str,
        config_updated_at: str,
    ) -> None:
        active = []
        if listen_enabled:
            active.append("聴く")
        if speak_enabled:
            active.append("話す")
        self._streams_label.configure(text=f"有効: {' / '.join(active) if active else 'なし'}")

        mode_lines = list(mode_summary)[: len(self._mode_labels)]
        while len(mode_lines) < len(self._mode_labels):
            mode_lines.append("")
        for label, text in zip(self._mode_labels, mode_lines):
            label.configure(text=text)

        config_lines = (
            pc_audio_label,
            mic_label,
            *device_summary,
            backend_summary,
            f"構成更新: {config_updated_at}",
        )
        for label, text in zip(self._config_labels, config_lines):
            label.configure(text=text)

    def set_quick_action_state(
        self,
        *,
        reload_enabled: bool,
        clear_enabled: bool,
        export_enabled: bool,
        helper_text: str = "",
    ) -> None:
        states = {
            "reload": reload_enabled,
            "clear": clear_enabled,
            "export": export_enabled,
        }
        for name, enabled in states.items():
            button = self._quick_action_buttons.get(name)
            if button is None:
                continue
            button.configure(state=("normal" if enabled else "disabled"))
        self._quick_action_helper.configure(text=helper_text)

    def quick_action_labels(self) -> tuple[str, ...]:
        return tuple(
            self._quick_action_buttons[name].cget("text")
            for name in self._quick_action_order
            if name in self._quick_action_buttons
        )

    def quick_action_states(self) -> dict[str, str]:
        return {
            name: str(self._quick_action_buttons[name].cget("state"))
            for name in self._quick_action_order
            if name in self._quick_action_buttons
        }

    def quick_action_helper_text(self) -> str:
        return str(self._quick_action_helper.cget("text"))

    def quick_action_grid_positions(self) -> dict[str, tuple[int, int]]:
        positions: dict[str, tuple[int, int]] = {}
        for name in self._quick_action_order:
            button = self._quick_action_buttons.get(name)
            if button is None:
                continue
            info = button.grid_info()
            positions[name] = (int(info["row"]), int(info["column"]))
        return positions

    def _on_frame_configure(self, event) -> None:
        self._layout_quick_actions(event.width)

    def _layout_quick_actions(self, width: int) -> None:
        ordered_buttons = [
            self._quick_action_buttons[name]
            for name in self._quick_action_order
            if name in self._quick_action_buttons
        ]
        if not ordered_buttons:
            return

        columns = 1 if width < 360 else 2
        for button in ordered_buttons:
            button.grid_forget()
            button.configure(width=18 if columns == 1 else 16)

        for index, button in enumerate(ordered_buttons):
            row = index if columns == 1 else index // columns
            column = 0 if columns == 1 else index % columns
            button.grid(row=row, column=column, sticky="ew", padx=(0, 8 if column == 0 else 0), pady=(0, 6))

    def set_blocker(self, message: str | None) -> None:
        if message:
            self._blocker_label.configure(text=message)
            self._blocker_frame.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 8))
        else:
            self._blocker_label.configure(text="")
            self._blocker_frame.grid_remove()

    def blocker_visible(self) -> bool:
        return bool(self._blocker_frame.winfo_manager())

    def dump_labels(self) -> str:
        parts = [
            self._streams_label.cget("text"),
            *(label.cget("text") for label in self._mode_labels),
            *(label.cget("text") for label in self._config_labels),
        ]
        return "\n".join(part for part in parts if part)
