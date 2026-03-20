"""WorkspacePanel — notebook workspace for auxiliary tools."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from realtime_translator.history import HistoryEntry


class WorkspacePanel:
    """再翻訳・返答アシスト・議事録を Notebook で切り替えるワークスペース。"""

    def __init__(self, parent, controller) -> None:
        self._controller = controller

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        self._notebook = ttk.Notebook(self.frame)
        self._notebook.grid(row=0, column=0, sticky="nsew")

        # Pending request tracking
        self._pending_retrans_id: str | None = None
        self._pending_assist_id: str | None = None
        self._pending_minutes_id: str | None = None

        # Listbox index -> seq mapping
        self._entry_map: dict[int, int] = {}
        self._tab_ids: dict[str, str] = {}

        self._retranslation_tab = ttk.Frame(self._notebook)
        self._assist_tab = ttk.Frame(self._notebook)
        self._minutes_tab = ttk.Frame(self._notebook)

        for tab in (self._retranslation_tab, self._assist_tab, self._minutes_tab):
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)

        self._notebook.add(self._retranslation_tab, text="再翻訳")
        self._notebook.add(self._assist_tab, text="返答アシスト")
        self._notebook.add(self._minutes_tab, text="議事録")
        for tab_id in self._notebook.tabs():
            self._tab_ids[self._notebook.tab(tab_id, "text")] = tab_id

        self._build_retrans_panel()
        self._build_assist_panel()
        self._build_minutes_panel()

    def tab_labels(self) -> list[str]:
        return [self._notebook.tab(tab_id, "text") for tab_id in self._notebook.tabs()]

    def active_tab_label(self) -> str:
        tab_id = self._notebook.select()
        return self._notebook.tab(tab_id, "text")

    def select_tab(self, label: str) -> None:
        self._notebook.select(self._tab_ids[label])

    # ------------------------------------------------------------------
    # 再翻訳タブ
    # ------------------------------------------------------------------

    def _build_retrans_panel(self) -> None:
        lf = ttk.LabelFrame(self._retranslation_tab, text="再翻訳")
        lf.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        lf.columnconfigure(0, weight=1)

        self._latest_label = ttk.Label(lf, text="最新: —", anchor="w")
        self._latest_label.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        list_frame = ttk.Frame(lf)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        lf.rowconfigure(1, weight=1)

        self._retrans_listbox = tk.Listbox(
            list_frame, height=6, selectmode="browse", font=("Meiryo UI", 9)
        )
        self._retrans_listbox.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self._retrans_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._retrans_listbox.configure(yscrollcommand=scrollbar.set)

        ctrl_frame = ttk.Frame(lf)
        ctrl_frame.grid(row=2, column=0, sticky="ew", padx=4, pady=2)

        ttk.Label(ctrl_frame, text="前後の範囲:").pack(side="left")
        self._range_var = tk.IntVar(value=3)
        spinbox = ttk.Spinbox(ctrl_frame, from_=1, to=20, width=4, textvariable=self._range_var)
        spinbox.pack(side="left", padx=(2, 4))
        ttk.Label(ctrl_frame, text="件").pack(side="left")

        self._retrans_btn = ttk.Button(ctrl_frame, text="再翻訳 実行", command=self._execute_retrans)
        self._retrans_btn.pack(side="left", padx=(8, 4))

        self._retrans_status = ttk.Label(ctrl_frame, text="")
        self._retrans_status.pack(side="left", padx=4)

        self._retrans_result_text = scrolledtext.ScrolledText(
            lf, height=8, state="disabled", wrap="word", font=("Meiryo UI", 9)
        )
        self._retrans_result_text.grid(row=3, column=0, sticky="nsew", padx=4, pady=(2, 4))

    # ------------------------------------------------------------------
    # 返答アシストタブ
    # ------------------------------------------------------------------

    def _build_assist_panel(self) -> None:
        lf = ttk.LabelFrame(self._assist_tab, text="返答アシスト")
        lf.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(1, weight=1)

        ctrl_frame = ttk.Frame(lf)
        ctrl_frame.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        self._assist_btn = ttk.Button(ctrl_frame, text="アシスト実行", command=self._execute_assist)
        self._assist_btn.pack(side="left")

        self._assist_status = ttk.Label(ctrl_frame, text="")
        self._assist_status.pack(side="left", padx=8)

        self._assist_result_text = scrolledtext.ScrolledText(
            lf, height=12, state="disabled", wrap="word", font=("Meiryo UI", 9)
        )
        self._assist_result_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))

    # ------------------------------------------------------------------
    # 議事録タブ
    # ------------------------------------------------------------------

    def _build_minutes_panel(self) -> None:
        lf = ttk.LabelFrame(self._minutes_tab, text="議事録")
        lf.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(1, weight=1)

        ctrl_frame = ttk.Frame(lf)
        ctrl_frame.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))

        self._minutes_btn = ttk.Button(ctrl_frame, text="議事録 生成", command=self._execute_minutes)
        self._minutes_btn.pack(side="left")

        self._minutes_export_btn = ttk.Button(ctrl_frame, text="エクスポート", command=self._export_minutes)
        self._minutes_export_btn.pack(side="left", padx=(4, 0))

        self._minutes_status = ttk.Label(ctrl_frame, text="")
        self._minutes_status.pack(side="left", padx=8)

        self._minutes_text = scrolledtext.ScrolledText(
            lf, height=12, wrap="word", font=("Meiryo UI", 9)
        )
        self._minutes_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))

    # ------------------------------------------------------------------
    # Public API — entry updates
    # ------------------------------------------------------------------

    def update_latest_entry(self, entry: "HistoryEntry") -> None:
        label = "PC音声" if entry.stream_id == "listen" else "マイク"
        orig = entry.original[:40]
        if len(entry.original) > 40:
            orig += "..."
        error_suffix = f" エラー: {entry.error}" if entry.error else ""
        self._latest_label.configure(
            text=f"最新: #{entry.seq} [{entry.timestamp}] {label}: {orig}{error_suffix}"
        )

        display = f"#{entry.seq} [{entry.timestamp}] {label}: {orig}{error_suffix}"
        idx = self._retrans_listbox.size()
        self._retrans_listbox.insert("end", display)
        self._entry_map[idx] = entry.seq

        self._retrans_listbox.selection_clear(0, "end")
        self._retrans_listbox.selection_set(idx)
        self._retrans_listbox.see(idx)

    def on_history_entry(self, entry: "HistoryEntry") -> None:
        self.update_latest_entry(entry)

    def refresh_history(self) -> None:
        self._retrans_listbox.delete(0, "end")
        self._entry_map.clear()

        for entry in self._controller.history.all_entries():
            label = "PC音声" if entry.stream_id == "listen" else "マイク"
            orig = entry.original[:40]
            if len(entry.original) > 40:
                orig += "..."
            error_suffix = f" エラー: {entry.error}" if entry.error else ""
            display = f"#{entry.seq} [{entry.timestamp}] {label}: {orig}{error_suffix}"
            idx = self._retrans_listbox.size()
            self._retrans_listbox.insert("end", display)
            self._entry_map[idx] = entry.seq

    # ------------------------------------------------------------------
    # 再翻訳
    # ------------------------------------------------------------------

    def _execute_retrans(self) -> None:
        sel = self._retrans_listbox.curselection()
        if sel:
            idx = sel[0]
        elif self._retrans_listbox.size() > 0:
            idx = self._retrans_listbox.size() - 1
        else:
            return

        seq = self._entry_map.get(idx)
        if seq is None:
            return

        entry = self._controller.history.get_by_seq(seq)
        if entry is not None and not entry.usable_for_downstream:
            self._retrans_status.configure(text=f"エラー: {entry.error}")
            self._retrans_btn.configure(state="normal")
            self._pending_retrans_id = None
            return

        batch_id = self._controller.request_retranslation(seq, self._range_var.get())
        self._pending_retrans_id = batch_id
        self._retrans_status.configure(text="再翻訳中...")
        self._retrans_btn.configure(state="disabled")

    def on_retrans_result(self, batch_id: str, seq: int, text: str) -> None:
        if batch_id != self._pending_retrans_id:
            return

        entry = self._controller.history.get_by_seq(seq)
        original_trans = entry.translation if entry else "?"

        result = f"#{seq} 元の訳文: {original_trans}\n    再翻訳: {text}"
        self._set_text(self._retrans_result_text, result)

        self._retrans_status.configure(text="再翻訳完了")
        self._retrans_btn.configure(state="normal")
        self._pending_retrans_id = None

    def on_retranslation_result(self, batch_id: str, seq: int, text: str) -> None:
        self.on_retrans_result(batch_id, seq, text)

    def on_retrans_error(self, batch_id: str, msg: str) -> None:
        if batch_id != self._pending_retrans_id:
            return
        self._retrans_status.configure(text=f"エラー: {msg}")
        self._retrans_btn.configure(state="normal")
        self._pending_retrans_id = None

    # ------------------------------------------------------------------
    # 返答アシスト
    # ------------------------------------------------------------------

    def _execute_assist(self) -> None:
        request_id = self._controller.request_reply_assist()
        self._pending_assist_id = request_id
        self._assist_status.configure(text="アシスト実行中...")
        self._assist_btn.configure(state="disabled")

    def on_assist_result(self, request_id: str, text: str) -> None:
        if request_id != self._pending_assist_id:
            return
        self._set_text(self._assist_result_text, text)
        self._assist_status.configure(text="完了")
        self._assist_btn.configure(state="normal")
        self._pending_assist_id = None

    def on_assist_error(self, request_id: str, msg: str) -> None:
        if request_id != self._pending_assist_id:
            return
        self._assist_status.configure(text=f"エラー: {msg}")
        self._assist_btn.configure(state="normal")
        self._pending_assist_id = None

    # ------------------------------------------------------------------
    # 議事録
    # ------------------------------------------------------------------

    def _execute_minutes(self) -> None:
        previous = self._minutes_text.get("1.0", "end-1c").strip()
        request_id = self._controller.request_minutes(previous_minutes=previous)
        self._pending_minutes_id = request_id
        self._minutes_status.configure(text="議事録生成中...")
        self._minutes_btn.configure(state="disabled")

    def on_minutes_result(self, request_id: str, text: str) -> None:
        if request_id != self._pending_minutes_id:
            return
        self._minutes_text.delete("1.0", "end")
        self._minutes_text.insert("1.0", text)
        self._minutes_status.configure(text="生成完了")
        self._minutes_btn.configure(state="normal")
        self._pending_minutes_id = None

    def on_minutes_error(self, request_id: str, msg: str) -> None:
        if request_id != self._pending_minutes_id:
            return
        self._minutes_status.configure(text=f"エラー: {msg}")
        self._minutes_btn.configure(state="normal")
        self._pending_minutes_id = None

    def _export_minutes(self) -> None:
        text = self._minutes_text.get("1.0", "end-1c").strip()
        if not text:
            self._minutes_status.configure(text="エクスポートするテキストがありません")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("テキストファイル", "*.txt"), ("すべてのファイル", "*.*")],
            initialfile=f"minutes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if not path:
            return

        Path(path).write_text(text, encoding="utf-8")
        self._minutes_status.configure(text=f"保存完了: {Path(path).name}")

    # ------------------------------------------------------------------
    # Public API — button states / reset / local errors
    # ------------------------------------------------------------------

    def set_button_states(
        self,
        retranslate_enabled: bool,
        assist_enabled: bool,
        minutes_enabled: bool,
    ) -> None:
        self._retrans_btn.configure(state="normal" if retranslate_enabled else "disabled")
        self._assist_btn.configure(state="normal" if assist_enabled else "disabled")
        self._minutes_btn.configure(state="normal" if minutes_enabled else "disabled")

    def on_local_error(self, tool_name: str, message: str) -> None:
        if tool_name == "retranslation":
            self._retrans_status.configure(text=f"エラー: {message}")
        elif tool_name == "assist":
            self._assist_status.configure(text=f"エラー: {message}")
        elif tool_name == "minutes":
            self._minutes_status.configure(text=f"エラー: {message}")

    def reset(self) -> None:
        self._pending_retrans_id = None
        self._pending_assist_id = None
        self._pending_minutes_id = None

        self._retrans_listbox.delete(0, "end")
        self._entry_map.clear()
        self._latest_label.configure(text="最新: —")
        self._retrans_status.configure(text="")
        self._set_text(self._retrans_result_text, "")

        self._assist_status.configure(text="")
        self._set_text(self._assist_result_text, "")

        self._minutes_status.configure(text="")
        self._minutes_text.delete("1.0", "end")

    @staticmethod
    def _set_text(widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
