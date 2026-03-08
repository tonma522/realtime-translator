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
    GEMINI_MODEL,
    OPENAI_CHAT_MODEL,
    OPENAI_STT_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_MODEL,
    OPENAI_STT_MODELS,
    pyaudio,
    _PTT_BINDINGS,
    _STREAM_META,
)
from .devices import enum_devices
from .controller import TranslatorController, StartConfig
from .config import save_config, load_config


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

    # ─────────────────────────── UI構築 ───────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ── バックエンド設定 ──
        backend_frame = ttk.LabelFrame(self.root, text="バックエンド設定")
        backend_frame.pack(fill="x", **pad)

        ttk.Label(backend_frame, text="STTバックエンド:").grid(row=0, column=0, sticky="w", **pad)
        self._stt_backend_var = tk.StringVar(value="Gemini (内蔵)")
        self._stt_backend_combo = ttk.Combobox(
            backend_frame, textvariable=self._stt_backend_var, state="readonly", width=20,
            values=["Gemini (内蔵)", "OpenAI Whisper", "ローカルWhisper", "OpenRouter"],
        )
        self._stt_backend_combo.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(backend_frame, text="LLMバックエンド:").grid(row=0, column=2, sticky="w", **pad)
        self._llm_backend_var = tk.StringVar(value="Gemini")
        self._llm_backend_combo = ttk.Combobox(
            backend_frame, textvariable=self._llm_backend_var, state="readonly", width=15,
            values=["Gemini", "OpenAI", "OpenRouter"],
        )
        self._llm_backend_combo.grid(row=0, column=3, sticky="w", **pad)
        backend_frame.columnconfigure(1, weight=1)

        # ── API設定 ──
        api_frame = ttk.LabelFrame(self.root, text="API設定")
        api_frame.pack(fill="x", **pad)

        # Gemini key row
        self._gemini_key_label = ttk.Label(api_frame, text="Gemini APIキー:")
        self._gemini_key_label.grid(row=0, column=0, sticky="w", **pad)
        self._api_key_var = tk.StringVar()
        self._api_entry = ttk.Entry(api_frame, textvariable=self._api_key_var, show="*", width=45)
        self._api_entry.grid(row=0, column=1, sticky="ew", **pad)

        # Gemini model
        self._gemini_model_label = ttk.Label(api_frame, text="モデル:")
        self._gemini_model_label.grid(row=0, column=2, sticky="w", **pad)
        self._gemini_model_var = tk.StringVar(value=GEMINI_MODEL)
        self._gemini_model_combo = ttk.Combobox(
            api_frame, textvariable=self._gemini_model_var, width=22,
            values=["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
        )
        self._gemini_model_combo.grid(row=0, column=3, sticky="w", **pad)

        # OpenAI key row
        self._openai_key_label = ttk.Label(api_frame, text="OpenAI APIキー:")
        self._openai_key_label.grid(row=1, column=0, sticky="w", **pad)
        self._openai_api_key_var = tk.StringVar()
        self._openai_api_entry = ttk.Entry(api_frame, textvariable=self._openai_api_key_var, show="*", width=45)
        self._openai_api_entry.grid(row=1, column=1, sticky="ew", **pad)

        # OpenAI model
        self._openai_model_label = ttk.Label(api_frame, text="モデル:")
        self._openai_model_label.grid(row=1, column=2, sticky="w", **pad)
        self._openai_chat_model_var = tk.StringVar(value=OPENAI_CHAT_MODEL)
        self._openai_model_combo = ttk.Combobox(
            api_frame, textvariable=self._openai_chat_model_var, width=22,
            values=["gpt-4o", "gpt-4o-mini", "gpt-4o-audio-preview"],
        )
        self._openai_model_combo.grid(row=1, column=3, sticky="w", **pad)

        # OpenRouter key row
        self._openrouter_key_label = ttk.Label(api_frame, text="OpenRouter APIキー:")
        self._openrouter_key_label.grid(row=2, column=0, sticky="w", **pad)
        self._openrouter_api_key_var = tk.StringVar()
        self._openrouter_api_entry = ttk.Entry(api_frame, textvariable=self._openrouter_api_key_var, show="*", width=45)
        self._openrouter_api_entry.grid(row=2, column=1, sticky="ew", **pad)

        # OpenRouter model
        self._openrouter_model_label = ttk.Label(api_frame, text="モデル:")
        self._openrouter_model_label.grid(row=2, column=2, sticky="w", **pad)
        self._openrouter_model_var = tk.StringVar(value=OPENROUTER_DEFAULT_MODEL)
        self._openrouter_model_combo = ttk.Combobox(
            api_frame, textvariable=self._openrouter_model_var, width=22,
            values=["google/gemini-2.0-flash-001", "google/gemini-2.5-flash", "anthropic/claude-3.5-sonnet"],
        )
        self._openrouter_model_combo.grid(row=2, column=3, sticky="w", **pad)

        # OpenAI STT model
        self._openai_stt_model_label = ttk.Label(api_frame, text="STTモデル:")
        self._openai_stt_model_label.grid(row=3, column=0, sticky="w", **pad)
        self._openai_stt_model_var = tk.StringVar(value=OPENAI_STT_DEFAULT_MODEL)
        self._openai_stt_model_combo = ttk.Combobox(
            api_frame, textvariable=self._openai_stt_model_var, state="readonly", width=20,
            values=list(OPENAI_STT_MODELS),
        )
        self._openai_stt_model_combo.grid(row=3, column=1, sticky="w", **pad)

        api_frame.columnconfigure(1, weight=1)

        # Dynamic visibility
        self._api_frame = api_frame
        self._backend_widgets = {
            "gemini": [self._gemini_key_label, self._api_entry, self._gemini_model_label, self._gemini_model_combo],
            "openai": [self._openai_key_label, self._openai_api_entry, self._openai_model_label, self._openai_model_combo],
            "openrouter": [self._openrouter_key_label, self._openrouter_api_entry, self._openrouter_model_label, self._openrouter_model_combo],
            "openai_stt": [self._openai_stt_model_label, self._openai_stt_model_combo],
        }

        def _update_backend_visibility(*_):
            stt = self._stt_backend_var.get()
            llm = self._llm_backend_var.get()
            needs_gemini = llm == "Gemini" or stt == "Gemini (内蔵)"
            needs_openai = llm == "OpenAI" or stt == "OpenAI Whisper"
            needs_openrouter = llm == "OpenRouter" or stt == "OpenRouter"
            needs_openai_stt = stt in ("OpenAI Whisper", "OpenRouter")

            for w in self._backend_widgets["gemini"]:
                w.grid() if needs_gemini else w.grid_remove()
            for w in self._backend_widgets["openai"]:
                w.grid() if needs_openai else w.grid_remove()
            for w in self._backend_widgets["openrouter"]:
                w.grid() if needs_openrouter else w.grid_remove()
            for w in self._backend_widgets["openai_stt"]:
                w.grid() if needs_openai_stt else w.grid_remove()

            # Update whisper/two-phase visibility
            is_external_stt = stt in ("OpenAI Whisper", "OpenRouter")
            if hasattr(self, "_two_phase_var"):
                if is_external_stt:
                    self._two_phase_var.set(False)

        self._stt_backend_var.trace_add("write", _update_backend_visibility)
        self._llm_backend_var.trace_add("write", _update_backend_visibility)
        # Initialize visibility
        _update_backend_visibility()

        # ── デバイス設定 ──
        dev_frame = ttk.LabelFrame(self.root, text="デバイス設定")
        dev_frame.pack(fill="x", **pad)
        ttk.Label(dev_frame, text="ループバック(聴く):").grid(row=0, column=0, sticky="w", **pad)
        self._loopback_var = tk.StringVar()
        self._loopback_combo = ttk.Combobox(dev_frame, textvariable=self._loopback_var, state="readonly", width=38)
        self._loopback_combo.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(dev_frame, text="更新", command=self._refresh_devices).grid(row=0, column=2, rowspan=2, **pad)
        ttk.Label(dev_frame, text="マイク(話す):").grid(row=1, column=0, sticky="w", **pad)
        self._mic_var = tk.StringVar()
        self._mic_combo = ttk.Combobox(dev_frame, textvariable=self._mic_var, state="readonly", width=38)
        self._mic_combo.grid(row=1, column=1, sticky="ew", **pad)
        dev_frame.columnconfigure(1, weight=1)

        # ── 翻訳コンテキスト ──
        ctx_frame = ttk.LabelFrame(self.root, text="翻訳コンテキスト（事前設定）")
        ctx_frame.pack(fill="x", **pad)
        self._context_text = tk.Text(ctx_frame, height=3, wrap="word")
        self._context_text.pack(fill="x", padx=8, pady=4)
        self._context_text.insert("end", "例: 製造業の生産管理会議。BOM、リードタイム、MRP等の用語が出る。")

        # ── チャンク間隔 + VAD ──
        interval_frame = ttk.LabelFrame(self.root, text="チャンク間隔")
        interval_frame.pack(fill="x", **pad)
        self._interval_var = tk.IntVar(value=5)
        self._interval_radios: list[ttk.Radiobutton] = []
        for label, val in [("3秒", 3), ("5秒", 5), ("8秒", 8)]:
            rb = ttk.Radiobutton(interval_frame, text=label, variable=self._interval_var, value=val)
            rb.pack(side="left", padx=12, pady=4)
            self._interval_radios.append(rb)
        ttk.Separator(interval_frame, orient="vertical").pack(side="left", fill="y", padx=8)
        self._vad_var = tk.BooleanVar(value=False)
        self._vad_cb = ttk.Checkbutton(interval_frame, text="VAD（発話検出）", variable=self._vad_var)
        self._vad_cb.pack(side="left", padx=4, pady=4)

        # ── 有効ストリーム ──
        stream_frame = ttk.LabelFrame(self.root, text="有効ストリーム")
        stream_frame.pack(fill="x", **pad)
        self._enable_listen_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(stream_frame, text="聴く (PC音声→日本語)", variable=self._enable_listen_var).pack(
            side="left", padx=12, pady=4)
        self._enable_speak_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(stream_frame, text="話す (マイク→英語)", variable=self._enable_speak_var).pack(
            side="left", padx=12, pady=4)
        self._ptt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(stream_frame, text="プッシュ・トゥ・トーク", variable=self._ptt_var).pack(
            side="left", padx=12, pady=4)
        self._two_phase_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(stream_frame, text="2フェーズ(STT→翻訳)", variable=self._two_phase_var).pack(
            side="left", padx=12, pady=4)

        # ── Whisper設定 ──
        whisper_frame = ttk.LabelFrame(self.root, text="Whisper設定（ローカルSTT）")
        whisper_frame.pack(fill="x", **pad)
        self._whisper_var = tk.BooleanVar(value=False)
        whisper_cb_state = "normal" if WHISPER_AVAILABLE else "disabled"
        ttk.Checkbutton(whisper_frame, text="ローカルWhisper使用", variable=self._whisper_var,
                        state=whisper_cb_state).pack(side="left", padx=8, pady=4)
        if not WHISPER_AVAILABLE:
            ttk.Label(whisper_frame, text="(pip install faster-whisper が必要)",
                      foreground="gray").pack(side="left")
        ttk.Label(whisper_frame, text="モデル:").pack(side="left", padx=(16, 2), pady=4)
        self._whisper_model_var = tk.StringVar(value="small")
        for m in ("tiny", "base", "small", "medium"):
            ttk.Radiobutton(whisper_frame, text=m, variable=self._whisper_model_var, value=m,
                            state=whisper_cb_state).pack(side="left", padx=2, pady=4)
        ttk.Label(whisper_frame, text="言語:").pack(side="left", padx=(16, 2), pady=4)
        self._whisper_lang_var = tk.StringVar(value="auto")
        lang_combo = ttk.Combobox(whisper_frame, textvariable=self._whisper_lang_var,
                                  values=["auto", "ja", "en"], state="readonly", width=6)
        lang_combo.pack(side="left", padx=2, pady=4)

        # ── 翻訳結果 ──
        result_frame = ttk.LabelFrame(self.root, text="翻訳結果")
        result_frame.pack(fill="both", expand=True, **pad)
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

        # ── ボタン行 ──
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self._start_btn = ttk.Button(btn_frame, text="▶ 翻訳開始", command=self._toggle)
        self._start_btn.pack(side="left", padx=4)
        ttk.Button(btn_frame, text="クリア", command=self._clear_result).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="エクスポート", command=self._export_result).pack(side="left", padx=4)
        self._save_btn = ttk.Button(btn_frame, text="設定保存", command=self._save_config)
        self._save_btn.pack(side="left", padx=4)
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

        def _on_ptt_toggle(*_):
            ptt_on = self._ptt_var.get()
            for rb in self._interval_radios:
                rb.config(state="disabled" if ptt_on else "normal")
            self._vad_cb.config(state="disabled" if ptt_on else "normal")
            if ptt_on:
                self._ptt_frame.pack(side="left", after=self._save_btn)
            else:
                self._ptt_frame.pack_forget()

        def _on_vad_toggle(*_):
            vad_on = self._vad_var.get()
            for rb in self._interval_radios:
                rb.config(state="disabled" if vad_on else "normal")

        self._ptt_var.trace_add("write", _on_ptt_toggle)
        self._vad_var.trace_add("write", _on_vad_toggle)

    # ─────────────────────────── デバイス ───────────────────────────

    def _refresh_devices(self) -> None:
        if not PYAUDIO_AVAILABLE:
            for combo in (self._loopback_combo, self._mic_combo):
                combo["values"] = ["pyaudiowpatch が未インストール"]
                combo.current(0)
            return
        self._loopback_devices = enum_devices(loopback=True, pa=self._pa)
        self._mic_devices = enum_devices(loopback=False, pa=self._pa)
        self._set_combo(self._loopback_combo, self._loopback_devices, "ループバックデバイスが見つかりません")
        self._set_combo(self._mic_combo, self._mic_devices, "マイクデバイスが見つかりません")
        self._restore_device_selection()

    def _set_combo(self, combo: ttk.Combobox, devices: list[dict], placeholder: str) -> None:
        combo["values"] = [d["name"] for d in devices] if devices else [placeholder]
        combo.current(0)

    def _get_device_index(self, combo: ttk.Combobox, devices: list[dict]) -> int | None:
        sel = combo.current()
        return devices[sel]["index"] if 0 <= sel < len(devices) else None

    def _restore_device_selection(self) -> None:
        for combo, devices, saved in [
            (self._loopback_combo, self._loopback_devices, self._saved_loopback_name),
            (self._mic_combo,      self._mic_devices,      self._saved_mic_name),
        ]:
            if saved:
                for i, d in enumerate(devices):
                    if d["name"] == saved:
                        combo.current(i)
                        break

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
        loopback_idx = self._get_device_index(self._loopback_combo, self._loopback_devices) if enable_listen else None
        mic_idx = self._get_device_index(self._mic_combo, self._mic_devices) if enable_speak else None
        whisper_lang = self._whisper_lang_var.get()

        # Map UI labels to backend identifiers
        stt_backend = self._STT_LABEL_TO_ID.get(self._stt_backend_var.get(), "gemini")
        llm_backend = self._LLM_LABEL_TO_ID.get(self._llm_backend_var.get(), "gemini")

        # request_whisper for backward compat with whisper STT backend
        request_whisper = stt_backend == "whisper"

        config = StartConfig(
            api_key=self._api_key_var.get().strip(),
            context=self._context_text.get("1.0", "end").strip(),
            chunk_seconds=self._interval_var.get(),
            enable_listen=enable_listen,
            enable_speak=enable_speak,
            loopback_device_index=loopback_idx,
            mic_device_index=mic_idx,
            ptt_enabled=self._ptt_var.get(),
            use_vad=self._vad_var.get(),
            request_whisper=request_whisper,
            request_two_phase=self._two_phase_var.get(),
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
        )
        self._controller.start(config)

        # UI updates after successful start
        self._start_btn.config(text="■ 翻訳停止")
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
        try:
            while True:
                item = self._ui_queue.get_nowait()
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
                    elif kind == "status":
                        _, msg = item
                        self._status_var.set(f"状態: {msg}")
                except Exception:
                    logging.exception("キューアイテム処理エラー: %r", item)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _on_partial_start(self, stream_id: str, ts: str) -> None:
        label, tag = _STREAM_META[stream_id]
        with self._editable_result():
            mark = self._result_text.index("end-1c")
            self._result_text.insert("end", f"[{ts}] {label}\n", tag)
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
        label, tag = _STREAM_META[stream_id]
        with self._editable_result():
            self._result_text.insert("end", f"[{ts}] {label}\n", tag)
            self._result_text.insert("end", f"原文: {text}\n", "original")

    def _append_error(self, msg: str) -> None:
        logging.error(msg)
        with self._editable_result():
            self._result_text.insert("end", f"[エラー] {msg}\n", "error")

    def _clear_result(self) -> None:
        with self._editable_result():
            self._result_text.delete("1.0", "end")

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
                "context": self._context_text.get("1.0", "end").strip(),
                "interval": self._interval_var.get(),
                "loopback_device_name": self._loopback_var.get(),
                "mic_device_name": self._mic_var.get(),
                "enable_listen": self._enable_listen_var.get(),
                "enable_speak": self._enable_speak_var.get(),
                "ptt_enabled": self._ptt_var.get(),
                "vad_enabled": self._vad_var.get(),
                "two_phase_enabled": self._two_phase_var.get(),
                "whisper_enabled": self._whisper_var.get(),
                "whisper_model": self._whisper_model_var.get(),
                "whisper_lang": self._whisper_lang_var.get(),
                "stt_backend": self._STT_LABEL_TO_ID.get(self._stt_backend_var.get(), "gemini"),
                "llm_backend": self._LLM_LABEL_TO_ID.get(self._llm_backend_var.get(), "gemini"),
                "gemini_model": self._gemini_model_var.get(),
                "openai_chat_model": self._openai_chat_model_var.get(),
                "openai_stt_model": self._openai_stt_model_var.get(),
                "openrouter_model": self._openrouter_model_var.get(),
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
        ctx = config.get("context", "")
        if ctx:
            self._context_text.delete("1.0", "end")
            self._context_text.insert("end", ctx)
        self._saved_loopback_name = config.get("loopback_device_name", "")
        self._saved_mic_name = config.get("mic_device_name", "")
        self._enable_listen_var.set(config.get("enable_listen", True))
        self._enable_speak_var.set(config.get("enable_speak", True))
        self._ptt_var.set(config.get("ptt_enabled", False))
        self._vad_var.set(config.get("vad_enabled", False))
        self._two_phase_var.set(config.get("two_phase_enabled", False))
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

    # ─────────────────────────── 終了処理 ───────────────────────────

    def on_close(self) -> None:
        self.root.withdraw()
        try:
            self._save_config()
        except Exception:
            logging.exception("on_close での設定保存に失敗")
        self._stop()
        if self._pa:
            self._pa.terminate()
            self._pa = None
        self.root.destroy()
