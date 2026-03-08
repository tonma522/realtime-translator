"""設定ウィンドウ — TranslatorApp の設定 UI を Toplevel に分離."""

import tkinter as tk
from tkinter import ttk

from .constants import (
    WHISPER_AVAILABLE,
    SILENCE_RMS_THRESHOLD,
    MIC_SILENCE_RMS_THRESHOLD,
    OPENAI_STT_MODELS,
)


class SettingsWindow:
    """Toplevel settings window.  All tk.*Var live on the app; this class
    only creates widgets bound to them."""

    def __init__(self, parent: tk.Tk, app) -> None:
        self._app = app
        self._win = tk.Toplevel(parent)
        self._win.title("設定")
        self._win.geometry("700x600")
        self._win.resizable(True, True)
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._api_rows: dict[str, list[tk.Widget]] = {}
        self._interval_radios: list[ttk.Radiobutton] = []
        self._vad_cb: ttk.Checkbutton | None = None

        container = ttk.Frame(self._win)
        container.pack(fill="both", expand=True, padx=8, pady=4)

        self._build_backend_section(container)
        self._build_api_section(container)
        self._build_device_section(container)
        self._build_context_section(container)
        self._build_chunk_vad_section(container)
        self._build_threshold_section(container)
        self._build_whisper_section(container)
        self._build_buttons(container)

        self._update_backend_visibility()
        self._register_traces()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return True if the window still exists."""
        try:
            return self._win.winfo_exists()
        except tk.TclError:
            return False

    def focus(self) -> None:
        """Bring window to front."""
        self._win.lift()
        self._win.focus_force()

    @property
    def loopback_combo(self) -> ttk.Combobox:
        return self._loopback_combo

    @property
    def mic_combo(self) -> ttk.Combobox:
        return self._mic_combo

    @property
    def context_text(self) -> tk.Text:
        return self._context_text

    def apply_recording_option_state(
        self, ptt_on: bool, vad_on: bool,
        interval_enabled: bool, two_phase_enabled: bool,
    ) -> None:
        """Update widget states for PTT/VAD/interval/two_phase."""
        for rb in self._interval_radios:
            rb.configure(state="normal" if interval_enabled else "disabled")
        if self._vad_cb is not None:
            self._vad_cb.configure(state="normal" if interval_enabled else "disabled")

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_backend_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="バックエンド設定")
        lf.pack(fill="x", pady=(0, 4))

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=4, pady=2)
        ttk.Label(row, text="STTバックエンド:").grid(row=0, column=0, sticky="w")
        stt_cb = ttk.Combobox(
            row, textvariable=self._app._stt_backend_var, state="readonly",
            values=["Gemini (内蔵)", "OpenAI Whisper", "ローカルWhisper", "OpenRouter"],
            width=24,
        )
        stt_cb.grid(row=0, column=1, padx=4)

        ttk.Label(row, text="LLMバックエンド:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        llm_cb = ttk.Combobox(
            row, textvariable=self._app._llm_backend_var, state="readonly",
            values=["Gemini", "OpenAI", "OpenRouter"],
            width=20,
        )
        llm_cb.grid(row=0, column=3, padx=4)

    def _build_api_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="API設定")
        lf.pack(fill="x", pady=(0, 4))

        # --- Gemini row ---
        gemini_row = ttk.Frame(lf)
        gemini_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(gemini_row, text="Gemini APIキー:").grid(row=0, column=0, sticky="w")
        ttk.Entry(gemini_row, textvariable=self._app._api_key_var, show="*", width=32).grid(
            row=0, column=1, padx=4)
        ttk.Label(gemini_row, text="モデル:").grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Combobox(
            gemini_row, textvariable=self._app._gemini_model_var, state="readonly",
            values=["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"], width=20,
        ).grid(row=0, column=3, padx=4)
        self._api_rows["gemini"] = [gemini_row]

        # --- OpenAI row ---
        openai_row = ttk.Frame(lf)
        openai_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(openai_row, text="OpenAI APIキー:").grid(row=0, column=0, sticky="w")
        ttk.Entry(openai_row, textvariable=self._app._openai_api_key_var, show="*", width=32).grid(
            row=0, column=1, padx=4)
        ttk.Label(openai_row, text="モデル:").grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Combobox(
            openai_row, textvariable=self._app._openai_chat_model_var, state="readonly",
            values=["gpt-4o", "gpt-4o-mini", "gpt-4o-audio-preview"], width=22,
        ).grid(row=0, column=3, padx=4)
        self._api_rows["openai"] = [openai_row]

        # --- OpenRouter row ---
        or_row = ttk.Frame(lf)
        or_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(or_row, text="OpenRouter APIキー:").grid(row=0, column=0, sticky="w")
        ttk.Entry(or_row, textvariable=self._app._openrouter_api_key_var, show="*", width=32).grid(
            row=0, column=1, padx=4)
        ttk.Label(or_row, text="モデル:").grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Combobox(
            or_row, textvariable=self._app._openrouter_model_var, state="readonly",
            values=["google/gemini-2.0-flash-001", "google/gemini-2.5-flash",
                    "anthropic/claude-3.5-sonnet"],
            width=30,
        ).grid(row=0, column=3, padx=4)
        self._api_rows["openrouter"] = [or_row]

        # --- OpenAI STT model row ---
        stt_row = ttk.Frame(lf)
        stt_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(stt_row, text="STTモデル:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            stt_row, textvariable=self._app._openai_stt_model_var, state="readonly",
            values=list(OPENAI_STT_MODELS), width=20,
        ).grid(row=0, column=1, padx=4)
        self._api_rows["openai_stt"] = [stt_row]

    def _build_device_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="デバイス設定")
        lf.pack(fill="x", pady=(0, 4))

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=4, pady=2)

        ttk.Label(row, text="ループバック(聴く):").grid(row=0, column=0, sticky="w")
        self._loopback_combo = ttk.Combobox(
            row, textvariable=self._app._loopback_var, state="readonly", width=40)
        self._loopback_combo.grid(row=0, column=1, padx=4)

        ttk.Label(row, text="マイク(話す):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._mic_combo = ttk.Combobox(
            row, textvariable=self._app._mic_var, state="readonly", width=40)
        self._mic_combo.grid(row=1, column=1, padx=4, pady=(4, 0))

        ttk.Button(row, text="更新", command=self._app._refresh_devices).grid(
            row=0, column=2, rowspan=2, padx=8)

        # Populate combos if device lists are already available
        if hasattr(self._app, "_loopback_devices") and self._app._loopback_devices:
            self._loopback_combo["values"] = [d["name"] for d in self._app._loopback_devices]
        if hasattr(self._app, "_mic_devices") and self._app._mic_devices:
            self._mic_combo["values"] = [d["name"] for d in self._app._mic_devices]

    def _build_context_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="翻訳コンテキスト")
        lf.pack(fill="x", pady=(0, 4))

        self._context_text = tk.Text(lf, height=3, wrap="word")
        self._context_text.pack(fill="x", padx=4, pady=4)

        # Populate from app._context
        ctx = getattr(self._app, "_context", "")
        if ctx:
            self._context_text.insert("1.0", ctx)
        else:
            self._context_text.insert(
                "1.0", "例: 製造業の生産管理会議。BOM、リードタイム、MRP等の用語が出る。")

    def _build_chunk_vad_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="チャンク間隔 + VAD")
        lf.pack(fill="x", pady=(0, 4))

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=4, pady=2)

        for i, (text, val) in enumerate([("1秒", 1), ("2秒", 2), ("3秒", 3), ("5秒", 5), ("8秒", 8)]):
            rb = ttk.Radiobutton(row, text=text, variable=self._app._interval_var, value=val)
            rb.grid(row=0, column=i, padx=4)
            self._interval_radios.append(rb)

        self._vad_cb = ttk.Checkbutton(row, text="VAD", variable=self._app._vad_var)
        self._vad_cb.grid(row=0, column=len(self._interval_radios), padx=(12, 4))

        # API interval slider (separate from chunk interval)
        api_row = ttk.Frame(lf)
        api_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(api_row, text="翻訳API呼び出し間隔:").grid(row=0, column=0, sticky="w")
        ttk.Scale(
            api_row, variable=self._app._api_interval_var,
            from_=0.0, to=5.0, orient="horizontal", length=200,
        ).grid(row=0, column=1, padx=4)
        api_lbl = ttk.Label(api_row, text="自動")
        api_lbl.grid(row=0, column=2, padx=4)

        def _update_api_interval_label(*_):
            v = self._app._api_interval_var.get()
            api_lbl.configure(text="自動" if v < 0.1 else f"{v:.1f}s")
        self._app._api_interval_var.trace_add("write", _update_api_interval_label)

    def _build_threshold_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="無音閾値（RMS）")
        lf.pack(fill="x", pady=(0, 4))

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=4, pady=2)

        # PC音声
        ttk.Label(row, text="PC音声:").grid(row=0, column=0, sticky="w")
        ttk.Scale(
            row, variable=self._app._threshold_listen_var,
            from_=10, to=1000, orient="horizontal", length=200,
        ).grid(row=0, column=1, padx=4)
        listen_lbl = ttk.Label(row, text=str(self._app._threshold_listen_var.get()))
        listen_lbl.grid(row=0, column=2, padx=4)

        # マイク
        ttk.Label(row, text="マイク:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Scale(
            row, variable=self._app._threshold_speak_var,
            from_=10, to=1000, orient="horizontal", length=200,
        ).grid(row=1, column=1, padx=4, pady=(4, 0))
        speak_lbl = ttk.Label(row, text=str(self._app._threshold_speak_var.get()))
        speak_lbl.grid(row=1, column=2, padx=4, pady=(4, 0))

        # Live value labels
        self._app._threshold_listen_var.trace_add(
            "write", lambda *_: listen_lbl.configure(
                text=str(int(self._app._threshold_listen_var.get()))))
        self._app._threshold_speak_var.trace_add(
            "write", lambda *_: speak_lbl.configure(
                text=str(int(self._app._threshold_speak_var.get()))))

    def _build_whisper_section(self, parent: ttk.Frame) -> None:
        lf = ttk.LabelFrame(parent, text="Whisper設定")
        lf.pack(fill="x", pady=(0, 4))

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=4, pady=2)

        st = "normal" if WHISPER_AVAILABLE else "disabled"
        ttk.Checkbutton(
            row, text="ローカルWhisper使用", variable=self._app._whisper_var, state=st,
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(row, text="モデル:").grid(row=0, column=1, padx=(12, 0))
        for i, m in enumerate(("tiny", "base", "small", "medium")):
            ttk.Radiobutton(
                row, text=m, variable=self._app._whisper_model_var, value=m,
            ).grid(row=0, column=2 + i, padx=2)

        ttk.Label(row, text="言語:").grid(row=0, column=6, padx=(12, 0))
        ttk.Combobox(
            row, textvariable=self._app._whisper_lang_var, state="readonly",
            values=["auto", "ja", "en"], width=6,
        ).grid(row=0, column=7, padx=4)

    def _build_buttons(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="設定保存", command=self._app._save_config).pack(
            side="left", padx=4)
        ttk.Button(row, text="閉じる", command=self._on_close).pack(
            side="right", padx=4)

    # ------------------------------------------------------------------
    # Backend visibility
    # ------------------------------------------------------------------

    def _register_traces(self) -> None:
        self._app._stt_backend_var.trace_add("write", lambda *_: self._update_backend_visibility())
        self._app._llm_backend_var.trace_add("write", lambda *_: self._update_backend_visibility())

    def _update_backend_visibility(self) -> None:
        stt = self._app._stt_backend_var.get()
        llm = self._app._llm_backend_var.get()

        show_gemini = (llm == "Gemini") or (stt == "Gemini (内蔵)")
        show_openai = (llm == "OpenAI") or (stt == "OpenAI Whisper")
        show_openrouter = (llm == "OpenRouter") or (stt == "OpenRouter")
        show_stt = stt in ("OpenAI Whisper", "OpenRouter")

        for w in self._api_rows.get("gemini", []):
            w.pack(fill="x", padx=4, pady=2) if show_gemini else w.pack_forget()
        for w in self._api_rows.get("openai", []):
            w.pack(fill="x", padx=4, pady=2) if show_openai else w.pack_forget()
        for w in self._api_rows.get("openrouter", []):
            w.pack(fill="x", padx=4, pady=2) if show_openrouter else w.pack_forget()
        for w in self._api_rows.get("openai_stt", []):
            w.pack(fill="x", padx=4, pady=2) if show_stt else w.pack_forget()

        # External STT disables two-phase
        if stt in ("OpenAI Whisper", "OpenRouter"):
            self._app._two_phase_var.set(False)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        # Persist context text back to app
        self._app._context = self._context_text.get("1.0", "end-1c").strip()
        self._app._save_config()
        self._win.destroy()
