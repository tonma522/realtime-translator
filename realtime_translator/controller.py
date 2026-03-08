"""UI非依存の翻訳コントローラー"""
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .assist import AssistWorker
from .audio import AudioCapture
from .api import ApiWorker, ApiRequest
from .history import TranslationHistory
from .retranslation import RetranslationWorker
from .whisper_stt import WhisperWorker
from .openai_llm import OpenAiLlmWorker
from .openai_stt import OpenAiSttWorker
from .prompts import build_prompt, build_stt_prompt
from .constants import (
    GENAI_AVAILABLE,
    OPENAI_AVAILABLE,
    WHISPER_AVAILABLE,
    genai,
    GEMINI_MODEL,
    OPENAI_CHAT_MODEL,
    OPENAI_STT_DEFAULT_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_DEFAULT_MODEL,
    MIN_API_INTERVAL_SEC,
    MIN_API_INTERVAL_BY_BACKEND,
    MIC_SILENCE_RMS_THRESHOLD,
    SILENCE_RMS_THRESHOLD,
)

AudioCaptureFactory = Callable[..., AudioCapture]
ApiWorkerFactory = Callable[..., ApiWorker]
WhisperWorkerFactory = Callable[..., WhisperWorker]
OpenAiLlmWorkerFactory = Callable[..., OpenAiLlmWorker]
OpenAiSttWorkerFactory = Callable[..., OpenAiSttWorker]
GenaiClientFactory = Callable[[str], Any]
OpenAiClientFactory = Callable[..., Any]
AssistWorkerFactory = Callable[..., AssistWorker]


@dataclass
class StartConfig:
    api_key: str
    context: str
    chunk_seconds: int
    enable_listen: bool
    enable_speak: bool
    loopback_device_index: int | None
    mic_device_index: int | None
    ptt_enabled: bool
    use_vad: bool
    request_whisper: bool
    request_two_phase: bool
    show_original: bool = True
    whisper_model: str = "small"
    whisper_language: str | None = None
    # Multi-backend fields (defaults = Gemini-only, backward compatible)
    stt_backend: str = "gemini"        # "gemini" | "openai" | "whisper" | "openrouter"
    llm_backend: str = "gemini"        # "gemini" | "openai" | "openrouter"
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    openai_stt_model: str = OPENAI_STT_DEFAULT_MODEL
    openai_chat_model: str = OPENAI_CHAT_MODEL
    openrouter_model: str = OPENROUTER_DEFAULT_MODEL
    gemini_model: str = GEMINI_MODEL
    silence_threshold_listen: int = SILENCE_RMS_THRESHOLD
    silence_threshold_speak: int = MIC_SILENCE_RMS_THRESHOLD
    custom_api_interval: float | None = None  # None = バックエンド自動判定


def _default_client_factory(api_key: str) -> Any:
    return genai.Client(api_key=api_key)


def _default_openai_client_factory(api_key: str, base_url: str | None = None) -> Any:
    from openai import OpenAI
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


class TranslatorController:
    """UI非依存の翻訳オーケストレーション"""

    def __init__(
        self,
        ui_queue: queue.Queue,
        capture_factory: AudioCaptureFactory = AudioCapture,
        api_worker_factory: ApiWorkerFactory = ApiWorker,
        whisper_worker_factory: WhisperWorkerFactory = WhisperWorker,
        client_factory: GenaiClientFactory | None = None,
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        openai_client_factory: OpenAiClientFactory | None = None,
        openai_llm_worker_factory: OpenAiLlmWorkerFactory | None = None,
        openai_stt_worker_factory: OpenAiSttWorkerFactory | None = None,
        assist_worker_factory: AssistWorkerFactory | None = None,
    ) -> None:
        self._ui_queue = ui_queue
        self._capture_factory = capture_factory
        self._api_worker_factory = api_worker_factory
        self._whisper_worker_factory = whisper_worker_factory
        self._client_factory = client_factory or _default_client_factory
        self._openai_client_factory = openai_client_factory or _default_openai_client_factory
        self._openai_llm_worker_factory = openai_llm_worker_factory or OpenAiLlmWorker
        self._openai_stt_worker_factory = openai_stt_worker_factory or OpenAiSttWorker
        self._assist_worker_factory = assist_worker_factory or AssistWorker
        self._on_error = on_error
        self._on_status = on_status

        self._running = False
        self._ptt_event = threading.Event()
        self._api_worker_listen: ApiWorker | OpenAiLlmWorker | None = None
        self._api_worker_speak: ApiWorker | OpenAiLlmWorker | None = None
        self._whisper_worker: WhisperWorker | None = None
        self._openai_stt_worker: OpenAiSttWorker | None = None
        self._retrans_worker: RetranslationWorker | None = None
        self._assist_worker: AssistWorker | None = None
        self._capture_listen: AudioCapture | None = None
        self._capture_speak: AudioCapture | None = None
        self._history = TranslationHistory()

        self._context = ""
        self._show_original = True
        self._use_two_phase = False
        self._use_whisper = False
        self._stt_backend = "gemini"
        self._llm_backend = "gemini"

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def can_ptt(self) -> bool:
        return self._running and self._capture_speak is not None

    @property
    def use_whisper(self) -> bool:
        return self._use_whisper

    @property
    def use_two_phase(self) -> bool:
        return self._use_two_phase

    @property
    def ptt_event(self) -> threading.Event:
        return self._ptt_event

    @property
    def history(self) -> TranslationHistory:
        return self._history

    def can_retranslate(self) -> bool:
        """再翻訳可能か（two-phase / 外部STT モードのみ）"""
        return self._running and (
            self._use_two_phase or self._use_whisper or self._stt_backend in ("openai", "openrouter")
        )

    def can_assist(self) -> bool:
        """返答アシスト / 議事録が利用可能か"""
        return self._running and len(self._history.all_entries()) > 0

    def request_reply_assist(self, n_history: int = 20) -> str:
        """返答アシストをリクエスト。request_id を返す"""
        if not self.can_assist() or self._assist_worker is None:
            return ""
        return self._assist_worker.submit("reply_assist", self._context, n_history=n_history)

    def request_minutes(self, previous_minutes: str = "") -> str:
        """議事録生成をリクエスト。request_id を返す"""
        if not self.can_assist() or self._assist_worker is None:
            return ""
        return self._assist_worker.submit("minutes", self._context, previous_minutes=previous_minutes)

    def request_retranslation(self, center_seq: int, n_surrounding: int) -> str:
        """再翻訳をリクエスト。batch_id を返す"""
        if not self.can_retranslate() or self._retrans_worker is None:
            return ""
        return self._retrans_worker.submit(center_seq, n_surrounding, self._context)

    def _notify_error(self, msg: str) -> None:
        if self._on_error:
            self._on_error(msg)

    def _notify_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)

    def start(self, config: StartConfig) -> None:
        llm_backend = config.llm_backend
        stt_backend = config.stt_backend

        # ── Validation ──
        self._validate_config(config)

        # Normalize options
        use_whisper = stt_backend == "whisper"
        use_openai_stt = stt_backend in ("openai", "openrouter")
        use_two_phase = config.request_two_phase and not use_whisper and not use_openai_stt
        use_vad = config.use_vad and not config.ptt_enabled

        self._context = config.context
        self._show_original = config.show_original
        self._use_two_phase = use_two_phase
        self._use_whisper = use_whisper
        self._stt_backend = stt_backend
        self._llm_backend = llm_backend
        self._ptt_event.clear()

        try:
            self._start_workers(config, llm_backend, stt_backend,
                                use_whisper, use_openai_stt, use_vad)
        except Exception:
            self._rollback_started_workers()
            raise

        self._running = True

    @staticmethod
    def _resolve_api_interval(backend: str, custom: float | None = None) -> float:
        """LLM ワーカーの min_interval_sec をバックエンド別に解決する"""
        if custom is not None:
            return max(0.0, custom)
        return MIN_API_INTERVAL_BY_BACKEND.get(backend, MIN_API_INTERVAL_SEC)

    def _start_workers(self, config: StartConfig, llm_backend: str,
                       stt_backend: str, use_whisper: bool,
                       use_openai_stt: bool, use_vad: bool) -> None:
        """ワーカー・キャプチャを生成・起動する（失敗時は呼び出し元がrollback）"""
        self._history.clear()
        interval = self._resolve_api_interval(llm_backend, config.custom_api_interval)
        # ── Create LLM workers based on backend ──
        if llm_backend == "gemini":
            client = self._client_factory(config.api_key)
            self._api_worker_listen = self._api_worker_factory(
                self._ui_queue, client, min_interval_sec=interval,
                label="ApiWorker-listen", model=config.gemini_model,
            )
            self._api_worker_speak = self._api_worker_factory(
                self._ui_queue, client, min_interval_sec=interval,
                label="ApiWorker-speak", model=config.gemini_model,
            )
        elif llm_backend == "openai":
            openai_client = self._openai_client_factory(config.openai_api_key)
            self._api_worker_listen = self._openai_llm_worker_factory(
                self._ui_queue, client=openai_client, min_interval_sec=interval,
                label="OpenAiLlm-listen", model=config.openai_chat_model,
            )
            self._api_worker_speak = self._openai_llm_worker_factory(
                self._ui_queue, client=openai_client, min_interval_sec=interval,
                label="OpenAiLlm-speak", model=config.openai_chat_model,
            )
        elif llm_backend == "openrouter":
            or_client = self._openai_client_factory(
                config.openrouter_api_key, base_url=OPENROUTER_BASE_URL,
            )
            self._api_worker_listen = self._openai_llm_worker_factory(
                self._ui_queue, client=or_client, min_interval_sec=interval,
                label="OpenRouter-listen", model=config.openrouter_model,
            )
            self._api_worker_speak = self._openai_llm_worker_factory(
                self._ui_queue, client=or_client, min_interval_sec=interval,
                label="OpenRouter-speak", model=config.openrouter_model,
            )

        self._api_worker_listen.start()
        self._api_worker_speak.start()

        # ── Create STT worker ──
        if use_whisper:
            self._whisper_worker = self._whisper_worker_factory(
                api_worker_listen=self._api_worker_listen,
                api_worker_speak=self._api_worker_speak,
                ui_queue=self._ui_queue,
                model_size=config.whisper_model,
                language=config.whisper_language,
                context=config.context,
            )
            self._whisper_worker.start()
        elif use_openai_stt:
            if stt_backend == "openrouter":
                stt_client = self._openai_client_factory(
                    config.openrouter_api_key, base_url=OPENROUTER_BASE_URL,
                )
            else:
                stt_client = self._openai_client_factory(config.openai_api_key)
            self._openai_stt_worker = self._openai_stt_worker_factory(
                api_worker_listen=self._api_worker_listen,
                api_worker_speak=self._api_worker_speak,
                ui_queue=self._ui_queue,
                client=stt_client,
                model=config.openai_stt_model,
                language=config.whisper_language,
                context=config.context,
            )
            self._openai_stt_worker.start()

        # ── Create audio captures ──
        for stream_id, idx in [("listen", config.loopback_device_index),
                                ("speak", config.mic_device_index)]:
            if idx is None:
                continue

            if use_whisper:
                def make_whisper_cb(sid: str) -> Callable[[bytes], None]:
                    return lambda wav: self._whisper_worker.submit(wav, sid)
                cb = make_whisper_cb(stream_id)
            elif use_openai_stt:
                def make_openai_stt_cb(sid: str) -> Callable[[bytes], None]:
                    return lambda wav: self._openai_stt_worker.submit(wav, sid)
                cb = make_openai_stt_cb(stream_id)
            else:
                def make_cb(sid: str) -> Callable[[bytes], None]:
                    return lambda wav: self.on_audio_chunk(wav, sid)
                cb = make_cb(stream_id)

            threshold = config.silence_threshold_speak if stream_id == "speak" else config.silence_threshold_listen
            ptt_ev = self._ptt_event if (stream_id == "speak" and config.ptt_enabled) else None

            def make_error_cb(sid: str):
                def _err_cb(msg: str):
                    self._ui_queue.put(("error", sid, msg))
                return _err_cb

            cap = self._capture_factory(
                idx, config.chunk_seconds, cb, stream_id,
                ptt_event=ptt_ev, use_vad=use_vad, silence_threshold=threshold,
                error_callback=make_error_cb(stream_id),
            )
            cap.start()
            setattr(self, f"_capture_{stream_id}", cap)

        # ── Create retranslation worker ──
        workers_list = [w for w in (self._api_worker_listen, self._api_worker_speak) if w]
        if llm_backend == "gemini":
            retrans_client_factory = lambda: self._client_factory(config.api_key)
            retrans_model = config.gemini_model
            retrans_api_key = config.api_key
        elif llm_backend == "openai":
            retrans_client_factory = lambda: self._openai_client_factory(config.openai_api_key)
            retrans_model = config.openai_chat_model
            retrans_api_key = config.openai_api_key
        else:  # openrouter
            retrans_client_factory = lambda: self._openai_client_factory(
                config.openrouter_api_key, base_url=OPENROUTER_BASE_URL)
            retrans_model = config.openrouter_model
            retrans_api_key = config.openrouter_api_key
        self._retrans_worker = RetranslationWorker(
            ui_queue=self._ui_queue,
            history=self._history,
            workers=workers_list,
            llm_backend=llm_backend,
            model=retrans_model,
            api_key=retrans_api_key,
            client_factory=retrans_client_factory,
        )
        self._retrans_worker.start()

        # ── Create assist worker ──
        # monitored_workers includes LLM + STT workers (not assist itself)
        monitored = list(workers_list)
        if self._whisper_worker:
            monitored.append(self._whisper_worker)
        if self._openai_stt_worker:
            monitored.append(self._openai_stt_worker)
        self._assist_worker = self._assist_worker_factory(
            ui_queue=self._ui_queue,
            history=self._history,
            monitored_workers=monitored,
            llm_backend=llm_backend,
            model=retrans_model,
            api_key=retrans_api_key,
            client_factory=retrans_client_factory,
        )
        self._assist_worker.start()

    def _rollback_started_workers(self) -> None:
        """起動途中で例外が発生した場合、既に起動済みのワーカーを停止する"""
        # stop() は _running ガードがあるので一時的に True にして再利用する
        self._running = True
        try:
            self.stop()
        except Exception:
            logging.exception("rollback: stop() に失敗")

    def _validate_config(self, config: StartConfig) -> None:
        """バリデーション: 起動前のチェック"""
        llm_backend = config.llm_backend
        stt_backend = config.stt_backend

        # Gemini backend requires google-genai
        if llm_backend == "gemini" and not GENAI_AVAILABLE:
            raise ValueError("google-genai が未インストールです。")

        # OpenAI/OpenRouter backends require openai package
        if llm_backend in ("openai", "openrouter") and not OPENAI_AVAILABLE:
            raise ValueError("openai パッケージが未インストールです (pip install openai)。")
        if stt_backend in ("openai", "openrouter") and not OPENAI_AVAILABLE:
            raise ValueError("openai パッケージが未インストールです (pip install openai)。")

        # Gemini API key
        if llm_backend == "gemini":
            if not config.api_key:
                raise ValueError("Gemini APIキーを入力してください。")
            if len(config.api_key) != 39 or not config.api_key.startswith("AI"):
                logging.warning(
                    "APIキーの形式が通常と異なります (長さ=%d, 先頭='%s')。",
                    len(config.api_key), config.api_key[:2],
                )
                self._notify_error(
                    "警告: APIキーの形式が通常と異なります。"
                    "Gemini APIキーは通常 'AI' で始まる39文字です。"
                )

        # OpenAI API key
        if llm_backend == "openai" or (stt_backend == "openai" and llm_backend != "openai"):
            if not config.openai_api_key:
                raise ValueError("OpenAI APIキーを入力してください。")

        # OpenRouter API key
        if llm_backend == "openrouter" or stt_backend == "openrouter":
            if not config.openrouter_api_key:
                raise ValueError("OpenRouter APIキーを入力してください。")

        # STT backend constraints
        if stt_backend == "openrouter" and llm_backend != "openrouter":
            raise ValueError("OpenRouter STTを使用するにはLLMバックエンドもOpenRouterにしてください。")

        if not config.enable_listen and not config.enable_speak:
            raise ValueError("「聴く」か「話す」を少なくとも1つ有効にしてください。")

        if config.enable_listen and config.loopback_device_index is None:
            raise ValueError("有効なループバックデバイスを選択してください。")
        if config.enable_speak and config.mic_device_index is None:
            raise ValueError("有効なマイクデバイスを選択してください。")

        if stt_backend == "whisper" and not WHISPER_AVAILABLE:
            raise ValueError("faster-whisper が未インストールです (pip install faster-whisper)。")

    def stop(self) -> None:
        if not self._running:
            return

        self._ptt_event.clear()

        # Phase 1: signal all
        captures = []
        for attr in ("_capture_listen", "_capture_speak"):
            cap = getattr(self, attr)
            if cap:
                cap.signal_stop()
                captures.append(cap)
            setattr(self, attr, None)

        whisper = self._whisper_worker
        if whisper:
            whisper.signal_stop()
            self._whisper_worker = None

        openai_stt = self._openai_stt_worker
        if openai_stt:
            openai_stt.signal_stop()
            self._openai_stt_worker = None

        retrans = self._retrans_worker
        if retrans:
            retrans.signal_stop()
            self._retrans_worker = None

        assist = self._assist_worker
        if assist:
            assist.signal_stop()
            self._assist_worker = None

        api_workers = []
        for w in (self._api_worker_listen, self._api_worker_speak):
            if w:
                w.signal_stop()
                api_workers.append(w)
        self._api_worker_listen = None
        self._api_worker_speak = None

        # Phase 2: join all
        for cap in captures:
            cap.join(timeout=3)
        if whisper:
            whisper.join(timeout=10)
        if openai_stt:
            openai_stt.join(timeout=10)
        if retrans:
            retrans.join(timeout=5)
        if assist:
            assist.join(timeout=5)
        for w in api_workers:
            w.join(timeout=10)

        # Drain queue
        while not self._ui_queue.empty():
            try:
                self._ui_queue.get_nowait()
            except queue.Empty:
                break

        self._running = False

    def toggle(self, config: StartConfig) -> None:
        if self._running:
            self.stop()
        else:
            self.start(config)

    def on_audio_chunk(self, wav_bytes: bytes, stream_id: str) -> None:
        worker = self._api_worker_listen if stream_id == "listen" else self._api_worker_speak
        if worker is None:
            return
        if self._use_two_phase:
            worker.submit(ApiRequest(
                wav_bytes=wav_bytes,
                prompt=build_stt_prompt(stream_id),
                stream_id=stream_id, phase=1, context=self._context,
            ))
        else:
            worker.submit(ApiRequest(
                wav_bytes=wav_bytes,
                prompt=build_prompt(stream_id, self._context, self._show_original),
                stream_id=stream_id, phase=0,
            ))

    def ptt_press(self) -> None:
        if self.can_ptt:
            self._ptt_event.set()

    def ptt_release(self) -> None:
        self._ptt_event.clear()
