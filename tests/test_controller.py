"""TranslatorControllerのテスト"""
import queue
import threading
from unittest.mock import MagicMock, patch, call

import pytest

from realtime_translator.controller import TranslatorController, StartConfig

# google-genai は未インストール環境でもテスト可能にする
pytestmark = pytest.mark.usefixtures("_genai_available")


@pytest.fixture(autouse=True)
def _genai_available():
    with patch("realtime_translator.controller.GENAI_AVAILABLE", True), \
         patch("realtime_translator.controller.OPENAI_AVAILABLE", True):
        yield


def _make_config(**overrides) -> StartConfig:
    defaults = dict(
        api_key="AI" + "x" * 37,
        context="テスト",
        chunk_seconds=5,
        enable_listen=True,
        enable_speak=True,
        loopback_device_index=0,
        mic_device_index=1,
        ptt_enabled=False,
        use_vad=False,
        request_whisper=False,
        request_two_phase=False,
    )
    defaults.update(overrides)
    return StartConfig(**defaults)


class FakeWorker:
    def __init__(self, *args, **kwargs):
        self.started = False
        self.stopped = False
        self.joined = False
        self.submitted = []
        self._label = kwargs.get("label", "")
        self._pending_requests = 0
        self._is_busy = False

    def start(self):
        self.started = True

    def signal_stop(self):
        self.stopped = True

    def join(self, timeout=10):
        self.joined = True

    def submit(self, req):
        self.submitted.append(req)

    @property
    def is_running(self):
        return self.started and not self.stopped

    @property
    def pending_requests(self):
        return self._pending_requests

    @property
    def is_busy(self):
        return self._is_busy


class FakeCapture:
    def __init__(self, *args, **kwargs):
        self.started = False
        self.stopped = False
        self.joined = False
        self.args = args
        self.kwargs = kwargs

    def start(self):
        self.started = True

    def signal_stop(self):
        self.stopped = True

    def join(self, timeout=3):
        self.joined = True


class FakeWhisperWorker:
    def __init__(self, **kwargs):
        self.started = False
        self.stopped = False
        self.joined = False
        self.submitted = []
        self.kwargs = kwargs

    def start(self):
        self.started = True

    def signal_stop(self):
        self.stopped = True

    def join(self, timeout=15):
        self.joined = True

    def submit(self, wav_bytes, stream_id):
        self.submitted.append((wav_bytes, stream_id))


class FakeOpenAiSttWorker:
    def __init__(self, **kwargs):
        self.started = False
        self.stopped = False
        self.joined = False
        self.submitted = []
        self.kwargs = kwargs

    def start(self):
        self.started = True

    def signal_stop(self):
        self.stopped = True

    def join(self, timeout=15):
        self.joined = True

    def submit(self, wav_bytes, stream_id):
        self.submitted.append((wav_bytes, stream_id))


def _make_controller(**kwargs):
    ui_queue = queue.Queue()
    defaults = dict(
        ui_queue=ui_queue,
        capture_factory=FakeCapture,
        api_worker_factory=FakeWorker,
        whisper_worker_factory=FakeWhisperWorker,
        client_factory=lambda key: MagicMock(),
        openai_client_factory=lambda key, base_url=None: MagicMock(),
        openai_llm_worker_factory=FakeWorker,
        openai_stt_worker_factory=FakeOpenAiSttWorker,
    )
    defaults.update(kwargs)
    ctrl = TranslatorController(**defaults)
    return ctrl, ui_queue


# ─────────────────────── Validation tests ───────────────────────

class TestValidation:
    def test_genai_unavailable_raises(self):
        ctrl, _ = _make_controller()
        with patch("realtime_translator.controller.GENAI_AVAILABLE", False):
            with pytest.raises(ValueError, match="google-genai"):
                ctrl.start(_make_config())

    def test_empty_api_key_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="APIキー"):
            ctrl.start(_make_config(api_key=""))

    def test_no_streams_enabled_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="少なくとも1つ"):
            ctrl.start(_make_config(enable_listen=False, enable_speak=False))

    def test_listen_no_device_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="ループバック"):
            ctrl.start(_make_config(enable_listen=True, loopback_device_index=None))

    def test_speak_no_device_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="マイク"):
            ctrl.start(_make_config(enable_speak=True, mic_device_index=None))

    @patch("realtime_translator.controller.WHISPER_AVAILABLE", False)
    def test_whisper_unavailable_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="faster-whisper"):
            ctrl.start(_make_config(request_whisper=True, stt_backend="whisper"))

    def test_api_key_format_warning_non_blocking(self):
        errors = []
        ctrl, _ = _make_controller(on_error=errors.append)
        ctrl.start(_make_config(api_key="bad_key"))
        assert ctrl.is_running
        assert any("形式" in e for e in errors)

    def test_valid_config_starts(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        assert ctrl.is_running


# ─────────────────────── Lifecycle tests ───────────────────────

class TestLifecycle:
    def test_start_creates_workers(self):
        workers = []
        def track_worker(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            workers.append(w)
            return w
        ctrl, _ = _make_controller(api_worker_factory=track_worker)
        ctrl.start(_make_config())
        assert len(workers) == 2
        assert all(w.started for w in workers)

    def test_start_creates_captures(self):
        captures = []
        def track_capture(*args, **kwargs):
            c = FakeCapture(*args, **kwargs)
            captures.append(c)
            return c
        ctrl, _ = _make_controller(capture_factory=track_capture)
        ctrl.start(_make_config())
        assert len(captures) == 2
        assert all(c.started for c in captures)

    def test_stop_signals_and_joins_workers(self):
        workers = []
        def track_worker(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            workers.append(w)
            return w
        captures = []
        def track_capture(*args, **kwargs):
            c = FakeCapture(*args, **kwargs)
            captures.append(c)
            return c
        ctrl, _ = _make_controller(
            api_worker_factory=track_worker, capture_factory=track_capture,
        )
        ctrl.start(_make_config())
        ctrl.stop()
        assert all(w.stopped for w in workers)
        assert all(w.joined for w in workers)
        assert all(c.stopped for c in captures)
        assert all(c.joined for c in captures)

    def test_stop_drains_queue(self):
        ctrl, q = _make_controller()
        ctrl.start(_make_config())
        q.put(("status", "test"))
        q.put(("error", "test", "msg"))
        ctrl.stop()
        assert q.empty()

    def test_stop_sets_not_running(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        ctrl.stop()
        assert not ctrl.is_running

    def test_toggle_starts_then_stops(self):
        ctrl, _ = _make_controller()
        config = _make_config()
        ctrl.toggle(config)
        assert ctrl.is_running
        ctrl.toggle(config)
        assert not ctrl.is_running


# ─────────────────────── Audio dispatch tests ───────────────────────

class TestAudioDispatch:
    def test_routes_listen_to_listen_worker(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        ctrl.on_audio_chunk(b"wav", "listen")
        assert len(ctrl._api_worker_listen.submitted) == 1
        assert ctrl._api_worker_listen.submitted[0].stream_id == "listen"

    def test_routes_speak_to_speak_worker(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        ctrl.on_audio_chunk(b"wav", "speak")
        assert len(ctrl._api_worker_speak.submitted) == 1
        assert ctrl._api_worker_speak.submitted[0].stream_id == "speak"

    def test_normal_mode_phase_0(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        ctrl.on_audio_chunk(b"wav", "listen")
        req = ctrl._api_worker_listen.submitted[0]
        assert req.phase == 0

    def test_two_phase_mode_phase_1(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config(request_two_phase=True))
        ctrl.on_audio_chunk(b"wav", "listen")
        req = ctrl._api_worker_listen.submitted[0]
        assert req.phase == 1

    def test_worker_none_guard(self):
        ctrl, _ = _make_controller()
        # on_audio_chunk without start should not crash
        ctrl.on_audio_chunk(b"wav", "listen")


# ─────────────────────── PTT tests ───────────────────────

class TestPTT:
    def test_ptt_press_sets_event(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config(ptt_enabled=True))
        ctrl.ptt_press()
        assert ctrl.ptt_event.is_set()

    def test_ptt_release_clears_event(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config(ptt_enabled=True))
        ctrl.ptt_press()
        ctrl.ptt_release()
        assert not ctrl.ptt_event.is_set()

    def test_can_ptt_false_when_not_running(self):
        ctrl, _ = _make_controller()
        assert not ctrl.can_ptt

    def test_ptt_press_guarded_when_not_running(self):
        ctrl, _ = _make_controller()
        ctrl.ptt_press()
        assert not ctrl.ptt_event.is_set()


# ─────────────────────── Whisper tests ───────────────────────

class TestWhisper:
    @patch("realtime_translator.controller.WHISPER_AVAILABLE", True)
    def test_whisper_creates_worker(self):
        whisper_workers = []
        def track_whisper(**kwargs):
            w = FakeWhisperWorker(**kwargs)
            whisper_workers.append(w)
            return w
        ctrl, _ = _make_controller(whisper_worker_factory=track_whisper)
        ctrl.start(_make_config(request_whisper=True, stt_backend="whisper"))
        assert len(whisper_workers) == 1
        assert whisper_workers[0].started

    @patch("realtime_translator.controller.WHISPER_AVAILABLE", True)
    def test_whisper_interval(self):
        workers = []
        def track_worker(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            w._min_interval = kwargs.get("min_interval_sec")
            workers.append(w)
            return w
        ctrl, _ = _make_controller(api_worker_factory=track_worker)
        ctrl.start(_make_config(request_whisper=True, stt_backend="whisper"))
        assert all(w._min_interval == 1.0 for w in workers)

    @patch("realtime_translator.controller.WHISPER_AVAILABLE", True)
    def test_whisper_disables_two_phase(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config(request_whisper=True, request_two_phase=True, stt_backend="whisper"))
        assert ctrl._use_whisper
        assert not ctrl._use_two_phase


# ─────────────────────── Shutdown edge cases ───────────────────────

class TestShutdownEdgeCases:
    def test_stop_when_not_running(self):
        ctrl, _ = _make_controller()
        ctrl.stop()  # should not crash

    def test_double_stop(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        ctrl.stop()
        ctrl.stop()  # should not crash

    def test_start_after_stop(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config())
        ctrl.stop()
        ctrl.start(_make_config())
        assert ctrl.is_running

    def test_listen_only(self):
        ctrl, _ = _make_controller()
        ctrl.start(_make_config(enable_speak=False, mic_device_index=None))
        assert ctrl.is_running
        assert not ctrl.can_ptt


# ─────────────────────── Callback tests ───────────────────────

class TestCallbacks:
    def test_on_error_called_for_format_warning(self):
        errors = []
        ctrl, _ = _make_controller(on_error=errors.append)
        ctrl.start(_make_config(api_key="short"))
        assert len(errors) >= 1

    def test_on_status_callable(self):
        statuses = []
        ctrl, _ = _make_controller(on_status=statuses.append)
        ctrl.start(_make_config())
        ctrl.stop()

    def test_none_callbacks_no_crash(self):
        ctrl, _ = _make_controller(on_error=None, on_status=None)
        ctrl.start(_make_config(api_key="short"))
        ctrl.stop()


# ─────────────────────── Multi-backend tests ───────────────────────

class TestMultiBackendValidation:
    def test_openai_backend_missing_key_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="OpenAI APIキー"):
            ctrl.start(_make_config(llm_backend="openai", openai_api_key=""))

    def test_openrouter_backend_missing_key_raises(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="OpenRouter APIキー"):
            ctrl.start(_make_config(llm_backend="openrouter", openrouter_api_key=""))

    def test_openai_unavailable_raises(self):
        ctrl, _ = _make_controller()
        with patch("realtime_translator.controller.OPENAI_AVAILABLE", False):
            with pytest.raises(ValueError, match="openai"):
                ctrl.start(_make_config(llm_backend="openai", openai_api_key="sk-test"))

    def test_openrouter_stt_requires_openrouter_llm(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="OpenRouter"):
            ctrl.start(_make_config(
                stt_backend="openrouter", llm_backend="gemini",
                openrouter_api_key="or-test",
            ))

    def test_openai_stt_requires_openai_key(self):
        ctrl, _ = _make_controller()
        with pytest.raises(ValueError, match="OpenAI APIキー"):
            ctrl.start(_make_config(
                stt_backend="openai", llm_backend="gemini",
                openai_api_key="",
            ))

    def test_gemini_backend_no_genai_raises(self):
        ctrl, _ = _make_controller()
        with patch("realtime_translator.controller.GENAI_AVAILABLE", False):
            with pytest.raises(ValueError, match="google-genai"):
                ctrl.start(_make_config(llm_backend="gemini"))


class TestMultiBackendRouting:
    def test_openai_backend_creates_openai_llm_workers(self):
        llm_workers = []
        def track_llm(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            llm_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_llm_worker_factory=track_llm)
        ctrl.start(_make_config(
            llm_backend="openai", openai_api_key="sk-test",
        ))
        assert len(llm_workers) == 2
        assert all(w.started for w in llm_workers)

    def test_openrouter_backend_creates_openai_llm_workers(self):
        llm_workers = []
        def track_llm(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            llm_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_llm_worker_factory=track_llm)
        ctrl.start(_make_config(
            llm_backend="openrouter", openrouter_api_key="or-test",
        ))
        assert len(llm_workers) == 2

    def test_openai_stt_backend_creates_stt_worker(self):
        stt_workers = []
        def track_stt(**kwargs):
            w = FakeOpenAiSttWorker(**kwargs)
            stt_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_stt_worker_factory=track_stt)
        ctrl.start(_make_config(
            stt_backend="openai", openai_api_key="sk-test",
        ))
        assert len(stt_workers) == 1
        assert stt_workers[0].started

    def test_openai_stt_interval_reduced(self):
        workers = []
        def track_worker(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            w._min_interval = kwargs.get("min_interval_sec")
            workers.append(w)
            return w
        ctrl, _ = _make_controller(api_worker_factory=track_worker)
        ctrl.start(_make_config(stt_backend="openai", openai_api_key="sk-test"))
        assert all(w._min_interval == 1.0 for w in workers)

    def test_gemini_model_passed_to_api_worker(self):
        workers = []
        def track_worker(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            w._model = kwargs.get("model")
            workers.append(w)
            return w
        ctrl, _ = _make_controller(api_worker_factory=track_worker)
        ctrl.start(_make_config(gemini_model="gemini-2.0-flash"))
        assert all(w._model == "gemini-2.0-flash" for w in workers)

    def test_openai_chat_model_passed(self):
        llm_workers = []
        def track_llm(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            w._model = kwargs.get("model")
            llm_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_llm_worker_factory=track_llm)
        ctrl.start(_make_config(
            llm_backend="openai", openai_api_key="sk-test",
            openai_chat_model="gpt-4o-mini",
        ))
        assert all(w._model == "gpt-4o-mini" for w in llm_workers)

    def test_openrouter_model_passed(self):
        llm_workers = []
        def track_llm(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            w._model = kwargs.get("model")
            llm_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_llm_worker_factory=track_llm)
        ctrl.start(_make_config(
            llm_backend="openrouter", openrouter_api_key="or-test",
            openrouter_model="google/gemini-2.5-flash",
        ))
        assert all(w._model == "google/gemini-2.5-flash" for w in llm_workers)


class TestMultiBackendShutdown:
    def test_openai_stt_shutdown(self):
        stt_workers = []
        def track_stt(**kwargs):
            w = FakeOpenAiSttWorker(**kwargs)
            stt_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_stt_worker_factory=track_stt)
        ctrl.start(_make_config(stt_backend="openai", openai_api_key="sk-test"))
        ctrl.stop()
        assert len(stt_workers) == 1
        assert stt_workers[0].stopped
        assert stt_workers[0].joined

    def test_openai_llm_shutdown(self):
        llm_workers = []
        def track_llm(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            llm_workers.append(w)
            return w
        ctrl, _ = _make_controller(openai_llm_worker_factory=track_llm)
        ctrl.start(_make_config(llm_backend="openai", openai_api_key="sk-test"))
        ctrl.stop()
        assert all(w.stopped for w in llm_workers)
        assert all(w.joined for w in llm_workers)

    def test_mixed_backend_full_shutdown(self):
        """OpenAI STT + Gemini LLM: all workers stopped"""
        api_workers = []
        stt_workers = []
        def track_api(*args, **kwargs):
            w = FakeWorker(*args, **kwargs)
            api_workers.append(w)
            return w
        def track_stt(**kwargs):
            w = FakeOpenAiSttWorker(**kwargs)
            stt_workers.append(w)
            return w
        ctrl, _ = _make_controller(
            api_worker_factory=track_api,
            openai_stt_worker_factory=track_stt,
        )
        ctrl.start(_make_config(stt_backend="openai", openai_api_key="sk-test"))
        ctrl.stop()
        assert all(w.stopped for w in api_workers)
        assert len(stt_workers) == 1
        assert stt_workers[0].stopped
