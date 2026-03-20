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
        on_clear=None,
        on_export=None,
        enable_listen_var: tk.BooleanVar | None = None,
        enable_speak_var: tk.BooleanVar | None = None,
    ) -> None:
        self.frame = ttk.LabelFrame(parent, text="セッション")
        self.frame.columnconfigure(0, weight=1)

        self._enable_listen_var = enable_listen_var or tk.BooleanVar(value=True)
        self._enable_speak_var = enable_speak_var or tk.BooleanVar(value=True)

        header = ttk.Frame(self.frame)
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self._start_button = ttk.Button(header, text="▶ 翻訳開始", command=on_toggle)
        self._start_button.pack(side="left")

        self._settings_button = ttk.Button(header, text="詳細設定", command=on_open_settings)
        self._settings_button.pack(side="left", padx=(6, 0))

        if on_clear is not None:
            ttk.Button(header, text="クリア", command=on_clear).pack(side="left", padx=(12, 0))
        if on_export is not None:
            ttk.Button(header, text="エクスポート", command=on_export).pack(side="left", padx=(6, 0))

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
        self._pc_audio_label = ttk.Label(summary_frame, text="PC音声: 英語→日本語", anchor="w")
        self._pc_audio_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._mic_label = ttk.Label(summary_frame, text="マイク: 日本語→英語", anchor="w")
        self._mic_label.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        self._mode_labels = [
            ttk.Label(summary_frame, text="録音モード: 通常", anchor="w"),
            ttk.Label(summary_frame, text="翻訳方式: 通常", anchor="w"),
            ttk.Label(summary_frame, text="原文表示: ON", anchor="w"),
        ]
        for index, label in enumerate(self._mode_labels, start=3):
            label.grid(row=index, column=0, sticky="ew", pady=(2, 0))

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
    ) -> None:
        active = []
        if listen_enabled:
            active.append("聴く")
        if speak_enabled:
            active.append("話す")
        self._streams_label.configure(text=f"有効: {' / '.join(active) if active else 'なし'}")
        self._pc_audio_label.configure(text=pc_audio_label)
        self._mic_label.configure(text=mic_label)

        mode_lines = list(mode_summary)[: len(self._mode_labels)]
        while len(mode_lines) < len(self._mode_labels):
            mode_lines.append("")
        for label, text in zip(self._mode_labels, mode_lines):
            label.configure(text=text)

    def set_blocker(self, message: str | None) -> None:
        if message:
            self._blocker_label.configure(text=message)
            self._blocker_frame.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 8))
        else:
            self._blocker_label.configure(text="")
            self._blocker_frame.grid_remove()

    def blocker_visible(self) -> bool:
        return bool(self._blocker_frame.winfo_manager())

    def dump_labels(self) -> str:
        parts = [
            self._streams_label.cget("text"),
            self._pc_audio_label.cget("text"),
            self._mic_label.cget("text"),
            *(label.cget("text") for label in self._mode_labels),
        ]
        return "\n".join(part for part in parts if part)
