"""tkinter UI"""
import logging
import queue
import tkinter as tk
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tkinter import ttk, scrolledtext, filedialog

from .constants import (
    PYAUDIO_AVAILABLE,
    OPENAI_AVAILABLE,
    WHISPER_AVAILABLE,
    SILENCE_SENTINEL,
    SILENCE_RMS_THRESHOLD,
    MIC_SILENCE_RMS_THRESHOLD,
    GEMINI_MODEL,
    OPENAI_CHAT_MODEL,
    OPENAI_STT_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_MODEL,
    OPENAI_STT_MODELS,
    pyaudio,
    _PTT_BINDINGS,
)
from .devices import enum_devices
from .controller import TranslatorController, StartConfig
from .config import save_config, load_config
from .settings_window import SettingsWindow
from .tools_panel import ToolsPanel
from .stream_modes import (
    get_stream_meta,
    label_to_translation_mode,
    normalize_translation_mode,
    split_stream_id,
    translation_mode_to_label,
)


def format_stream_header(
    source_stream_id: str,
    virtual_stream_id: str,
    resolved_direction: str | None,
) -> str:
    if resolved_direction is None and virtual_stream_id.endswith("_auto"):
        return "PC音声 同時翻訳" if source_stream_id == "listen" else "マイク 同時翻訳"

    label = "PC音声" if source_stream_id == "listen" else "マイク"
    direction = "英語→日本語" if resolved_direction == "en_ja" else "日本語→英語"
    return f"{label} {direction}"


class TranslatorApp:
    _STT_LABEL_TO_ID = {
        "Gemini (内蔵)": "gemini",
        "OpenAI Whisper": "openai",
        "ローカルWhisper": "whisper",
        "OpenRouter": "openrouter",
    }
    _LLM_LABEL_TO_ID = {
        "Gemini": "gemini", "OpenAI": "openai", "OpenRouter": "openrouter",
    }
    _STT_ID_TO_LABEL = {v: k for k, v in _STT_LABEL_TO_ID.items()}
    _LLM_ID_TO_LABEL = {v: k for k, v in _LLM_LABEL_TO_ID.items()}

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("双方向リアルタイム音声翻訳")
        self.root.resizable(True, True)

        self._ui_queue: queue.Queue = queue.Queue()
        self._controller = TranslatorController(
            self._ui_queue,
            on_error=lambda msg: self._append_error(msg),
            on_status=lambda msg: self._status_var.set(f"状態: {msg}") if hasattr(self, "_status_var") else None,
        )

        self._stream_buffers: dict[str, dict] = {}
        self._loopback_devices: list[dict] = []
        self._mic_devices: list[dict] = []
        self._saved_loopback_name: str = ""
        self._saved_mic_name: str = ""
        self._pa = None
        self._settings_win: SettingsWindow | None = None
        self._tools_panel: ToolsPanel | None = None

        try:
            self._build_ui()
            self._load_config()
            self._poll_queue()
            # Defer audio initialization so the window is visible first
            self.root.after(1, self._deferred_init)
        except Exception:
            if self._pa:
                self._pa.terminate()
                self._pa = None
            raise

    def _deferred_init(self) -> None:
        """Initialize PyAudio and populate device lists after the window is visible."""
        self._status_var.set("状態: 初期化中...")
        self.root.update_idletasks()
        try:
            if PYAUDIO_AVAILABLE:
                self._pa = pyaudio.PyAudio()
            self._refresh_devices()
            self._status_var.set("状態: 待機中")
        except Exception as e:
            logging.exception("音声デバイス初期化エラー")
            self._status_var.set("状態: 初期化エラー")
            self._append_error(f"音声デバイス初期化エラー: {e}")

    # ─────────────────────────── 変数初期化 ───────────────────────────

    def _create_variables(self) -> None:
        self._stt_backend_var = tk.StringVar(value="Gemini (内蔵)")
        self._llm_backend_var = tk.StringVar(value="Gemini")
        self._api_key_var = tk.StringVar()
        self._openai_api_key_var = tk.StringVar()
        self._openrouter_api_key_var = tk.StringVar()
        self._gemini_model_var = tk.StringVar(value=GEMINI_MODEL)
        self._openai_chat_model_var = tk.StringVar(value=OPENAI_CHAT_MODEL)
        self._openai_stt_model_var = tk.StringVar(value=OPENAI_STT_DEFAULT_MODEL)
        self._openrouter_model_var = tk.StringVar(value=OPENROUTER_DEFAULT_MODEL)
        self._loopback_var = tk.StringVar()
        self._mic_var = tk.StringVar()
        self._interval_var = tk.IntVar(value=5)
        self._vad_var = tk.BooleanVar(value=False)
        self._enable_listen_var = tk.BooleanVar(value=True)
        self._enable_speak_var = tk.BooleanVar(value=True)
        self._ptt_var = tk.BooleanVar(value=False)
        self._two_phase_var = tk.BooleanVar(value=False)
        self._show_original_var = tk.BooleanVar(value=True)
        self._pc_audio_mode_var = tk.StringVar(value=translation_mode_to_label("en_ja"))
        self._mic_mode_var = tk.StringVar(value=translation_mode_to_label("ja_en"))
        self._whisper_var = tk.BooleanVar(value=False)
        self._whisper_model_var = tk.StringVar(value="small")
        self._whisper_lang_var = tk.StringVar(value="auto")
        self._threshold_listen_var = tk.IntVar(value=SILENCE_RMS_THRESHOLD)
        self._threshold_speak_var = tk.IntVar(value=MIC_SILENCE_RMS_THRESHOLD)
        self._api_interval_var = tk.DoubleVar(value=0.0)  # 0.0 = 自動（バックエンド判定）
        self._context: str = ""

    # ─────────────────────────── UI構築 ───────────────────────────

    def _build_ui(self) -> None:
        self._create_variables()
        self.root.geometry("1100x750")
        pad = {"padx": 8, "pady": 4}

        # ── 有効ストリーム ──
        stream_frame = ttk.LabelFrame(self.root, text="有効ストリーム")
        stream_frame.pack(fill="x", **pad)
        ttk.Checkbutton(stream_frame, text="聴く (PC音声)", variable=self._enable_listen_var).pack(
            side="left", padx=12, pady=4)
        ttk.Checkbutton(stream_frame, text="話す (マイク)", variable=self._enable_speak_var).pack(
            side="left", padx=12, pady=4)
        ttk.Checkbutton(stream_frame, text="プッシュ・トゥ・トーク", variable=self._ptt_var).pack(
            side="left", padx=12, pady=4)
        ttk.Checkbutton(stream_frame, text="2フェーズ(STT→翻訳)", variable=self._two_phase_var).pack(
            side="left", padx=12, pady=4)
        self._two_phase_warning = ttk.Label(stream_frame, text="応答が遅くなります", foreground="red")
        def _on_two_phase_toggle(*_):
            if self._two_phase_var.get():
                self._two_phase_warning.pack(side="left", padx=(0, 8))
            else:
                self._two_phase_warning.pack_forget()
        self._two_phase_var.trace_add("write", _on_two_phase_toggle)
        ttk.Checkbutton(stream_frame, text="原文も表示", variable=self._show_original_var).pack(
            side="left", padx=12, pady=4)

        # ── PanedWindow (vertical, 7:3) ──
        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        # Upper: translation results
        result_frame = ttk.LabelFrame(paned, text="翻訳結果")
        self._result_text = scrolledtext.ScrolledText(
            result_frame, wrap="word", state="disabled", height=16, font=("Meiryo UI", 11))
        self._result_text.pack(fill="both", expand=True, padx=4, pady=4)
        for tag, opts in [
            ("stream_listen", {"foreground": "#1565C0", "font": ("Meiryo UI", 10, "bold")}),
            ("stream_speak",  {"foreground": "#E65100", "font": ("Meiryo UI", 10, "bold")}),
            ("original",      {"foreground": "#555555", "font": ("Meiryo UI", 10, "italic")}),
            ("translation",   {"foreground": "#000000", "font": ("Meiryo UI", 12, "bold")}),
            ("error",         {"foreground": "#B71C1C", "font": ("Meiryo UI", 10)}),
            ("separator",     {"foreground": "#cccccc"}),
        ]:
            self._result_text.tag_configure(tag, **opts)
        paned.add(result_frame, weight=7)

        # Lower: tools panel
        self._tools_panel = ToolsPanel(paned, self._controller)
        paned.add(self._tools_panel.frame, weight=3)

        # ── ボタン行 ──
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self._start_btn = ttk.Button(btn_frame, text="▶ 翻訳開始", command=self._toggle)
        self._start_btn.pack(side="left", padx=4)
        ttk.Button(btn_frame, text="クリア", command=self._clear_result).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="エクスポート", command=self._export_result).pack(side="left", padx=4)
        self._settings_btn = ttk.Button(btn_frame, text="設定", command=self._open_settings)
        self._settings_btn.pack(side="left", padx=4)
        self._ptt_frame = ttk.Frame(btn_frame)
        self._ptt_btn = tk.Button(
            self._ptt_frame, text="🎙 録音 (押して話す)",
            bg="#FF8C00", fg="white", relief="raised",
            state="disabled", cursor="hand2",
        )
        self._ptt_btn.pack(padx=8)
        self._ptt_btn.bind("<ButtonPress-1>",   lambda e: self._ptt_press())
        self._ptt_btn.bind("<ButtonRelease-1>", lambda e: self._ptt_release())
        self._status_var = tk.StringVar(value="状態: 待機中")
        ttk.Label(btn_frame, textvariable=self._status_var).pack(side="left", padx=16)

        self._ptt_var.trace_add("write", lambda *_: self._sync_recording_option_state())
        self._vad_var.trace_add("write", lambda *_: self._sync_recording_option_state())

    # ─────────────────────────── 設定ウィンドウ ───────────────────────────

    def _open_settings(self) -> None:
        if self._settings_win and self._settings_win.is_open():
            self._settings_win.focus()
            return
        self._settings_win = SettingsWindow(self.root, self)
        # Populate device combos if available
        if self._loopback_devices or self._mic_devices:
            self._refresh_devices()
        self._sync_recording_option_state()

    def _sync_recording_option_state(self) -> None:
        ptt_on = self._ptt_var.get()
        vad_on = self._vad_var.get() and not ptt_on
        interval_enabled = not ptt_on and not vad_on
        stt = self._stt_backend_var.get()
        is_external_stt = stt in ("OpenAI Whisper", "OpenRouter")
        two_phase_enabled = not is_external_stt

        # PTT frame visibility
        if ptt_on:
            self._ptt_frame.pack(side="left", after=self._settings_btn)
        else:
            self._ptt_frame.pack_forget()

        # Update settings window if open
        if self._settings_win and self._settings_win.is_open():
            self._settings_win.apply_recording_option_state(
                ptt_on, vad_on, interval_enabled, two_phase_enabled)

    def _sync_tool_states(self) -> None:
        if self._tools_panel is None:
            return
        self._tools_panel.set_button_states(
            retranslate_enabled=self._controller.can_retranslate(),
            assist_enabled=self._controller.can_assist(),
            minutes_enabled=self._controller.can_assist(),
        )

    # ─────────────────────────── デバイス ───────────────────────────

    def _refresh_devices(self) -> None:
        if not PYAUDIO_AVAILABLE:
            return
        self._loopback_devices = enum_devices(loopback=True, pa=self._pa)
        self._mic_devices = enum_devices(loopback=False, pa=self._pa)
        if self._settings_win and self._settings_win.is_open():
            self._set_combo(self._settings_win.loopback_combo, self._loopback_devices, "ループバックデバイスが見つかりません")
            self._set_combo(self._settings_win.mic_combo, self._mic_devices, "マイクデバイスが見つかりません")
            self._restore_device_selection()

    def _set_combo(self, combo: ttk.Combobox, devices: list[dict], placeholder: str) -> None:
        combo["values"] = [d["name"] for d in devices] if devices else [placeholder]
        combo.current(0)

    def _get_device_index(self, var: tk.StringVar, devices: list[dict]) -> int | None:
        name = var.get()
        for d in devices:
            if d["name"] == name:
                return d["index"]
        return None

    def _restore_device_selection(self) -> None:
        for var, devices, saved in [
            (self._loopback_var, self._loopback_devices, self._saved_loopback_name),
            (self._mic_var, self._mic_devices, self._saved_mic_name),
        ]:
            if saved:
                for d in devices:
                    if d["name"] == saved:
                        var.set(saved)
                        break

    # ─────────────────────────── コンテキスト ───────────────────────────

    def _get_current_context(self) -> str:
        if self._settings_win and self._settings_win.is_open():
            return self._settings_win.context_text.get("1.0", "end-1c").strip()
        return self._context

    # ─────────────────────────── 翻訳制御 ───────────────────────────

    def _toggle(self) -> None:
        if self._controller.is_running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        try:
            self._start_inner()
        except ValueError as e:
            self._append_error(str(e))
        except Exception as e:
            logging.exception("_start() で予期しないエラー")
            self._append_error(f"起動エラー: {e}")

    def _start_inner(self) -> None:
        enable_listen = self._enable_listen_var.get()
        enable_speak = self._enable_speak_var.get()
        loopback_idx = self._get_device_index(self._loopback_var, self._loopback_devices) if enable_listen else None
        mic_idx = self._get_device_index(self._mic_var, self._mic_devices) if enable_speak else None
        whisper_lang = self._whisper_lang_var.get()

        # Map UI labels to backend identifiers
        stt_backend = self._STT_LABEL_TO_ID.get(self._stt_backend_var.get(), "gemini")
        llm_backend = self._LLM_LABEL_TO_ID.get(self._llm_backend_var.get(), "gemini")

        # request_whisper for backward compat with whisper STT backend
        request_whisper = stt_backend == "whisper"

        context = self._get_current_context()

        config = StartConfig(
            api_key=self._api_key_var.get().strip(),
            context=context,
            chunk_seconds=self._interval_var.get(),
            enable_listen=enable_listen,
            enable_speak=enable_speak,
            loopback_device_index=loopback_idx,
            mic_device_index=mic_idx,
            ptt_enabled=self._ptt_var.get(),
            use_vad=self._vad_var.get(),
            request_whisper=request_whisper,
            request_two_phase=self._two_phase_var.get(),
            show_original=self._show_original_var.get(),
            pc_audio_mode=normalize_translation_mode(
                label_to_translation_mode(self._pc_audio_mode_var.get(), "en_ja"),
                "en_ja",
            ),
            mic_mode=normalize_translation_mode(
                label_to_translation_mode(self._mic_mode_var.get(), "ja_en"),
                "ja_en",
            ),
            whisper_model=self._whisper_model_var.get(),
            whisper_language=None if whisper_lang == "auto" else whisper_lang,
            stt_backend=stt_backend,
            llm_backend=llm_backend,
            openai_api_key=self._openai_api_key_var.get().strip(),
            openrouter_api_key=self._openrouter_api_key_var.get().strip(),
            openai_stt_model=self._openai_stt_model_var.get(),
            openai_chat_model=self._openai_chat_model_var.get(),
            openrouter_model=self._openrouter_model_var.get(),
            gemini_model=self._gemini_model_var.get(),
            silence_threshold_listen=self._threshold_listen_var.get(),
            silence_threshold_speak=self._threshold_speak_var.get(),
            custom_api_interval=self._api_interval_var.get() or None,
        )
        self._controller.start(config)

        # UI updates after successful start
        self._start_btn.config(text="■ 翻訳停止")
        self._tools_panel.reset()
        self._sync_tool_states()
        streams = [s for s, en in [("聴く", enable_listen), ("話す", enable_speak)] if en]
        mode = "Whisper" if self._controller.use_whisper else ("2フェーズ" if self._controller.use_two_phase else "通常")
        self._status_var.set(f"状態: 翻訳中... ({'+'.join(streams)}, {mode})")

        if enable_speak and config.ptt_enabled:
            self._ptt_btn.config(state="normal")
            def _maybe_ptt_press(e):
                if isinstance(e.widget, (tk.Entry, tk.Text, ttk.Entry)):
                    return
                self._ptt_press()
            def _maybe_ptt_release(e):
                if isinstance(e.widget, (tk.Entry, tk.Text, ttk.Entry)):
                    return
                self._ptt_release()
            handlers = [_maybe_ptt_press, _maybe_ptt_release, lambda e: self._ptt_release()]
            for event, handler in zip(_PTT_BINDINGS, handlers):
                self.root.bind(event, handler)

    def _stop(self) -> None:
        for event in _PTT_BINDINGS:
            self.root.unbind(event)
        self._ptt_btn.config(state="disabled", text="🎙 録音 (押して話す)", bg="#FF8C00")

        self._controller.stop()
        self._stream_buffers.clear()

        self._start_btn.config(text="▶ 翻訳開始")
        self._tools_panel.reset()
        self._sync_tool_states()
        self._status_var.set("状態: 停止中")

    def _ptt_press(self) -> None:
        if self._controller.can_ptt:
            self._controller.ptt_press()
            self._ptt_btn.config(text="🔴 録音中...", bg="#CC0000")
            self._status_var.set("状態: 🎙 録音中 (Space/ボタンを離すと送信)")

    def _ptt_release(self) -> None:
        self._controller.ptt_release()
        if self._ptt_btn["state"] != "disabled":
            self._ptt_btn.config(text="🎙 録音 (押して話す)", bg="#FF8C00")
            if self._controller.is_running:
                self._status_var.set("状態: 翻訳中...")

    # ─────────────────────────── キューポーリング・UI更新 ───────────────────────────

    @contextmanager
    def _editable_result(self):
        self._result_text.config(state="normal")
        try:
            yield
        finally:
            self._result_text.see("end")
            self._result_text.config(state="disabled")

    def _poll_queue(self) -> None:
        had_items = False
        try:
            while True:
                item = self._ui_queue.get_nowait()
                had_items = True
                try:
                    kind = item[0]
                    if kind == "partial_start":
                        _, stream_id, ts = item
                        self._on_partial_start(stream_id, ts)
                    elif kind == "partial":
                        _, stream_id, text = item
                        self._on_partial(stream_id, text)
                    elif kind == "partial_end":
                        _, stream_id = item
                        self._on_partial_end(stream_id)
                    elif kind == "transcript":
                        _, stream_id, ts, text = item
                        self._on_transcript(stream_id, ts, text)
                    elif kind == "error":
                        _, stream_id, msg = item
                        self._append_error(f"[{stream_id}] {msg}")
                    elif kind == "translation_done":
                        if len(item) == 5:
                            _, stream_id, ts, original, translation = item
                            source_stream_id, mode = split_stream_id(stream_id)
                            virtual_stream_id = stream_id
                            resolved_direction = None if mode == "auto" else mode
                            error = None
                        else:
                            _, source_stream_id, virtual_stream_id, resolved_direction, ts, original, translation, error = item
                        entry = self._controller.history.append(
                            source_stream_id,
                            ts,
                            original,
                            translation,
                            virtual_stream_id=virtual_stream_id,
                            resolved_direction=resolved_direction,
                            error=error,
                        )
                        self._tools_panel.update_latest_entry(entry)
                        self._sync_tool_states()
                    elif kind == "retrans_result":
                        _, batch_id, seq, text = item
                        self._tools_panel.on_retrans_result(batch_id, seq, text)
                    elif kind == "retrans_error":
                        _, batch_id, msg = item
                        self._tools_panel.on_retrans_error(batch_id, msg)
                    elif kind == "assist_result":
                        _, request_id, request_type, text = item
                        if request_type == "reply_assist":
                            self._tools_panel.on_assist_result(request_id, text)
                        elif request_type == "minutes":
                            self._tools_panel.on_minutes_result(request_id, text)
                    elif kind == "assist_error":
                        _, request_id, request_type, msg = item
                        if request_type == "reply_assist":
                            self._tools_panel.on_assist_error(request_id, msg)
                        elif request_type == "minutes":
                            self._tools_panel.on_minutes_error(request_id, msg)
                    elif kind == "status":
                        _, msg = item
                        self._status_var.set(f"状態: {msg}")
                except Exception:
                    logging.exception("キューアイテム処理エラー: %r", item)
        except queue.Empty:
            pass
        # Adaptive polling: always 10ms during translation, 50ms when idle
        if self._controller.is_running:
            interval = 10
        else:
            interval = 10 if had_items else 50
        self.root.after(interval, self._poll_queue)

    def _flush_active_partials(self) -> None:
        """進行中のpartialストリームをすべて確定する（新しいブロック挿入前に呼ぶ）"""
        for sid in list(self._stream_buffers):
            self._on_partial_end(sid)

    def _on_partial_start(self, stream_id: str, ts: str) -> None:
        self._flush_active_partials()
        label, tag, langs = get_stream_meta(stream_id)
        with self._editable_result():
            mark = self._result_text.index("end-1c")
            self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
        self._stream_buffers[stream_id] = {"chunks": [], "mark": mark}

    def _on_partial(self, stream_id: str, text: str) -> None:
        if stream_id not in self._stream_buffers:
            return
        self._stream_buffers[stream_id]["chunks"].append(text)
        with self._editable_result():
            self._result_text.insert("end", text)

    def _on_partial_end(self, stream_id: str) -> None:
        buf = self._stream_buffers.pop(stream_id, None)
        if buf is None:
            return
        full_text = "".join(buf["chunks"])
        with self._editable_result():
            if SILENCE_SENTINEL in full_text:
                self._result_text.delete(buf["mark"], "end")
            else:
                self._result_text.insert("end", "\n" + "─" * 50 + "\n", "separator")

    def _on_transcript(self, stream_id: str, ts: str, text: str) -> None:
        if not self._show_original_var.get():
            return
        self._flush_active_partials()
        label, tag, langs = get_stream_meta(stream_id)
        with self._editable_result():
            self._result_text.insert("end", f"[{ts}] {label} {langs}\n", tag)
            self._result_text.insert("end", f"原文: {text}\n", "original")

    def _append_error(self, msg: str) -> None:
        logging.error(msg)
        with self._editable_result():
            self._result_text.insert("end", f"[エラー] {msg}\n", "error")

    def _clear_result(self) -> None:
        with self._editable_result():
            self._result_text.delete("1.0", "end")
        self._controller.history.clear()
        self._tools_panel.refresh_history()
        self._sync_tool_states()

    def _export_result(self) -> None:
        text = self._result_text.get("1.0", "end").strip()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("テキストファイル", "*.txt")],
            initialfile=f"translation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self._status_var.set(f"状態: エクスポート完了 → {Path(path).name}")

    # ─────────────────────────── 設定永続化 ───────────────────────────

    def _save_config(self) -> None:
        try:
            config = {
                "api_key": self._api_key_var.get(),
                "openai_api_key": self._openai_api_key_var.get(),
                "openrouter_api_key": self._openrouter_api_key_var.get(),
                "context": self._get_current_context(),
                "interval": self._interval_var.get(),
                "loopback_device_name": self._loopback_var.get(),
                "mic_device_name": self._mic_var.get(),
                "enable_listen": self._enable_listen_var.get(),
                "enable_speak": self._enable_speak_var.get(),
                "ptt_enabled": self._ptt_var.get(),
                "vad_enabled": self._vad_var.get(),
                "two_phase_enabled": self._two_phase_var.get(),
                "show_original": self._show_original_var.get(),
                "pc_audio_mode": label_to_translation_mode(self._pc_audio_mode_var.get(), "en_ja"),
                "mic_mode": label_to_translation_mode(self._mic_mode_var.get(), "ja_en"),
                "whisper_enabled": self._whisper_var.get(),
                "whisper_model": self._whisper_model_var.get(),
                "whisper_lang": self._whisper_lang_var.get(),
                "stt_backend": self._STT_LABEL_TO_ID.get(self._stt_backend_var.get(), "gemini"),
                "llm_backend": self._LLM_LABEL_TO_ID.get(self._llm_backend_var.get(), "gemini"),
                "gemini_model": self._gemini_model_var.get(),
                "openai_chat_model": self._openai_chat_model_var.get(),
                "openai_stt_model": self._openai_stt_model_var.get(),
                "openrouter_model": self._openrouter_model_var.get(),
                "silence_threshold_listen": self._threshold_listen_var.get(),
                "silence_threshold_speak": self._threshold_speak_var.get(),
                "api_interval": self._api_interval_var.get(),
            }
            save_config(config)
            self._status_var.set("状態: 設定を保存しました")
        except Exception as e:
            self._append_error(f"設定保存失敗: {e}")

    def _load_config(self) -> None:
        config = load_config()
        if not config:
            return
        self._api_key_var.set(config.get("api_key", ""))
        self._openai_api_key_var.set(config.get("openai_api_key", ""))
        self._openrouter_api_key_var.set(config.get("openrouter_api_key", ""))
        self._interval_var.set(config.get("interval", 5))
        self._context = config.get("context", "")
        if self._settings_win and self._settings_win.is_open():
            self._settings_win.context_text.delete("1.0", "end")
            self._settings_win.context_text.insert("end", self._context)
        self._saved_loopback_name = config.get("loopback_device_name", "")
        self._saved_mic_name = config.get("mic_device_name", "")
        self._enable_listen_var.set(config.get("enable_listen", True))
        self._enable_speak_var.set(config.get("enable_speak", True))
        self._ptt_var.set(config.get("ptt_enabled", False))
        self._vad_var.set(config.get("vad_enabled", False))
        self._two_phase_var.set(config.get("two_phase_enabled", False))
        self._show_original_var.set(config.get("show_original", True))
        self._pc_audio_mode_var.set(translation_mode_to_label(config.get("pc_audio_mode", "en_ja")))
        self._mic_mode_var.set(translation_mode_to_label(config.get("mic_mode", "ja_en")))
        self._whisper_var.set(config.get("whisper_enabled", False))
        self._whisper_model_var.set(config.get("whisper_model", "small"))
        self._whisper_lang_var.set(config.get("whisper_lang", "auto"))
        stt_id = config.get("stt_backend", "gemini")
        self._stt_backend_var.set(self._STT_ID_TO_LABEL.get(stt_id, stt_id))
        llm_id = config.get("llm_backend", "gemini")
        self._llm_backend_var.set(self._LLM_ID_TO_LABEL.get(llm_id, llm_id))
        self._gemini_model_var.set(config.get("gemini_model", GEMINI_MODEL))
        self._openai_chat_model_var.set(config.get("openai_chat_model", OPENAI_CHAT_MODEL))
        self._openai_stt_model_var.set(config.get("openai_stt_model", OPENAI_STT_DEFAULT_MODEL))
        self._openrouter_model_var.set(config.get("openrouter_model", OPENROUTER_DEFAULT_MODEL))
        self._threshold_listen_var.set(config.get("silence_threshold_listen", SILENCE_RMS_THRESHOLD))
        self._threshold_speak_var.set(config.get("silence_threshold_speak", MIC_SILENCE_RMS_THRESHOLD))
        self._api_interval_var.set(config.get("api_interval", 0.0))
        self._sync_recording_option_state()

    # ─────────────────────────── 終了処理 ───────────────────────────

    def on_close(self) -> None:
        self.root.withdraw()
        try:
            if self._settings_win and self._settings_win.is_open():
                self._settings_win._on_close()
            self._save_config()
        except Exception:
            logging.exception("on_close での設定保存に失敗")
        self._stop()
        if self._pa:
            self._pa.terminate()
            self._pa = None
        self.root.destroy()
