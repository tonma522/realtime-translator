"""Translation timeline panel with status bar and transcript area."""

from __future__ import annotations

import tkinter as tk
from contextlib import contextmanager
from tkinter import scrolledtext, ttk

from .constants import SILENCE_SENTINEL
from .stream_modes import get_stream_meta


class TranslationTimelinePanel:
    def __init__(self, parent) -> None:
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self._status_var = tk.StringVar(value="状態: 待機中")
        self._status_label = ttk.Label(self.frame, textvariable=self._status_var, anchor="w")
        self._status_label.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        self._result_text = scrolledtext.ScrolledText(
            self.frame, wrap="word", state="disabled", height=16, font=("Meiryo UI", 11)
        )
        self._result_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))
        for tag, opts in [
            ("stream_listen", {"foreground": "#1565C0", "font": ("Meiryo UI", 10, "bold")}),
            ("stream_speak", {"foreground": "#E65100", "font": ("Meiryo UI", 10, "bold")}),
            ("original", {"foreground": "#555555", "font": ("Meiryo UI", 10, "italic")}),
            ("translation", {"foreground": "#000000", "font": ("Meiryo UI", 12, "bold")}),
            ("error", {"foreground": "#B71C1C", "font": ("Meiryo UI", 10)}),
            ("separator", {"foreground": "#cccccc"}),
        ]:
            self._result_text.tag_configure(tag, **opts)

        self._stream_buffers: dict[str, dict] = {}

    @property
    def status_var(self) -> tk.StringVar:
        return self._status_var

    @property
    def result_text(self) -> scrolledtext.ScrolledText:
        return self._result_text

    def status_text(self) -> str:
        return self._status_var.get()

    def dump_text(self) -> str:
        return self._result_text.get("1.0", "end-1c")

    def set_global_status(self, status_kind: str, message: str) -> None:
        del status_kind
        rendered = message if message.startswith("状態: ") else f"状態: {message}"
        self._status_var.set(rendered)

    @contextmanager
    def _editable_result(self):
        self._result_text.config(state="normal")
        try:
            yield
        finally:
            self._result_text.see("end")
            self._result_text.config(state="disabled")

    def flush_active_partials(self) -> None:
        for sid in list(self._stream_buffers):
            self.on_partial_end(sid)

    def on_partial_start(self, stream_id: str, ts: str) -> None:
        self.flush_active_partials()
        label, tag, langs = get_stream_meta(stream_id)
        with self._editable_result():
            mark = self._result_text.index("end-1c")
            self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
        self._stream_buffers[stream_id] = {"chunks": [], "mark": mark}

    def on_partial(self, stream_id: str, text: str) -> None:
        if stream_id not in self._stream_buffers:
            return
        self._stream_buffers[stream_id]["chunks"].append(text)
        with self._editable_result():
            self._result_text.insert("end", text)

    def on_partial_end(self, stream_id: str) -> None:
        buf = self._stream_buffers.pop(stream_id, None)
        if buf is None:
            return
        full_text = "".join(buf["chunks"])
        with self._editable_result():
            if SILENCE_SENTINEL in full_text:
                self._result_text.delete(buf["mark"], "end")
            else:
                self._result_text.insert("end", "\n" + "─" * 50 + "\n", "separator")

    def on_transcript(self, stream_id: str, ts: str, text: str) -> None:
        self.flush_active_partials()
        label, tag, langs = get_stream_meta(stream_id)
        with self._editable_result():
            self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
            self._result_text.insert("end", f"原文: {text}\n", "original")

    def on_runtime_error(self, stream_id: str | None, message: str) -> None:
        prefix = f"[{stream_id}] " if stream_id else ""
        with self._editable_result():
            self._result_text.insert("end", f"[エラー] {prefix}{message}\n", "error")
