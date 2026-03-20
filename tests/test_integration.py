"""ApiWorker 統合テスト: audio-chunk -> ApiWorker -> UI-queue パイプライン"""
import queue
import time
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import realtime_translator.api as api_module
from realtime_translator.api import ApiWorker, ApiRequest
from realtime_translator.constants import SILENCE_SENTINEL
from realtime_translator.history import TranslationHistory

# genai_types が None の環境でも動作するようモックを用意
_mock_genai_types = MagicMock()
_mock_genai_types.Part.from_bytes.return_value = b"mock-audio-part"


@pytest.fixture(autouse=True)
def _patch_genai_types():
    """全テストで genai_types をモックに差し替え"""
    with patch.object(api_module, "genai_types", _mock_genai_types):
        yield


class _FakeChunk:
    """generate_content_stream が返すチャンクを模倣"""

    def __init__(self, text: str):
        self._text = text

    @property
    def text(self):
        return self._text


class _FakeChunkNoText:
    """chunk.text が ValueError を送出するケース"""

    @property
    def text(self):
        raise ValueError("no text")


def _make_mock_client(chunks_list):
    """モッククライアントを生成。chunks_list はチャンクのリスト。"""
    client = MagicMock()
    client.models.generate_content_stream.return_value = iter(chunks_list)
    return client


def _drain_ui_queue(ui_queue: queue.Queue, timeout: float = 2.0):
    """UI キューから全メッセージを回収"""
    messages = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            messages.append(ui_queue.get(timeout=0.1))
        except queue.Empty:
            # キューが空の状態が少し続いたら終了
            if messages:
                break
    return messages


def _make_poll_queue_app():
    from realtime_translator.app import TranslatorApp

    app = object.__new__(TranslatorApp)
    app._ui_queue = queue.Queue()
    app._controller = MagicMock()
    app._controller.history = TranslationHistory()
    app._controller.is_running = False
    app._controller.can_retranslate.return_value = True
    app._controller.can_assist.return_value = True
    app._tools_panel = MagicMock()
    app._sync_tool_states = MagicMock()
    app.root = MagicMock()
    return app


class TestEndToEnd:
    """Phase 0/2 ストリーミングの統合テスト"""

    def test_streaming_produces_partial_sequence(self):
        """Phase 0 リクエスト → partial_start, partial, partial_end の順で UI キューに入る"""
        chunks = [_FakeChunk("Hello "), _FakeChunk("World")]
        client = _make_mock_client(chunks)

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="translate this",
                stream_id="listen",
                phase=0,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        assert len(messages) >= 3
        assert messages[0][0] == "partial_start"
        assert messages[0][1] == "listen"
        # 中間メッセージは partial
        partials = [m for m in messages if m[0] == "partial"]
        assert len(partials) == 2
        assert partials[0][2] == "Hello "
        assert partials[1][2] == "World"
        # partial_end が含まれる
        types = [m[0] for m in messages]
        assert "partial_end" in types

    def test_empty_chunks_skipped(self):
        """空テキストやValueErrorチャンクはスキップされる"""
        chunks = [_FakeChunkNoText(), _FakeChunk(""), _FakeChunk("ok")]
        client = _make_mock_client(chunks)

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="test",
                stream_id="listen",
                phase=0,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        partials = [m for m in messages if m[0] == "partial"]
        assert len(partials) == 1
        assert partials[0][2] == "ok"

    def test_phase2_text_only_no_audio(self):
        """Phase 2 はテキストのみ（wav_bytes=None）でストリーミングする"""
        chunks = [_FakeChunk("Translation result")]
        client = _make_mock_client(chunks)

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=None,
                prompt="translate: hello",
                stream_id="speak",
                phase=2,
                transcript="hello",
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        # contents にオーディオパートがないことを確認
        call_args = client.models.generate_content_stream.call_args
        contents = call_args.kwargs.get("contents", call_args[1].get("contents"))
        assert len(contents) == 1  # テキストのみ、オーディオなし
        # ストリーミングシーケンスは正常
        types = [m[0] for m in messages]
        assert "partial_start" in types
        assert "partial_end" in types


class TestErrorPropagation:
    """エラー伝播テスト"""

    def test_streaming_exception_produces_error_message(self):
        """ストリーミング中の例外 → ("error", stream_id, msg) が UI キューに入る"""
        client = MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError("API limit exceeded")

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="test",
                stream_id="listen",
                phase=0,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        errors = [m for m in messages if m[0] == "error"]
        assert len(errors) == 1
        assert errors[0][1] == "listen"
        assert "API limit exceeded" in errors[0][2]

    def test_mid_stream_exception_sends_error_and_partial_end(self):
        """ストリーミング途中で例外 → error + partial_end が送られる"""
        def failing_stream(*args, **kwargs):
            yield _FakeChunk("start")
            raise RuntimeError("connection lost")

        client = MagicMock()
        client.models.generate_content_stream.side_effect = failing_stream

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="test",
                stream_id="listen",
                phase=0,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        types = [m[0] for m in messages]
        assert "partial_start" in types
        assert "error" in types
        assert "partial_end" in types

    def test_phase1_exception_produces_error(self):
        """Phase 1 で例外 → error メッセージが UI キューに入る"""
        client = MagicMock()
        client.models.generate_content_stream.side_effect = RuntimeError("phase1 fail")

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="transcribe",
                stream_id="listen",
                phase=1,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        errors = [m for m in messages if m[0] == "error"]
        assert len(errors) == 1
        assert "phase1 fail" in errors[0][2]


class TestPhaseChaining:
    """Phase 1 → Phase 2 自動チェーンのテスト"""

    def test_phase1_triggers_phase2(self):
        """Phase 1 の文字起こし結果が Phase 2 翻訳リクエストとして自動送信される"""
        # Phase 1: non-streaming
        phase1_response = MagicMock()
        phase1_response.text = "Hello world"

        client = MagicMock()
        client.models.generate_content.return_value = phase1_response
        # Phase 2: streaming
        client.models.generate_content_stream.return_value = iter([_FakeChunk("こんにちは世界")])

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="transcribe",
                stream_id="listen",
                phase=1,
                context="meeting",
            )
            worker.submit(req)
            # Phase1 + Phase2 の両方の結果を待つ
            messages = _drain_ui_queue(ui_queue, timeout=3.0)
        finally:
            worker.stop()

        # Phase 1: transcript メッセージ
        transcripts = [m for m in messages if m[0] == "transcript"]
        assert len(transcripts) == 1
        assert transcripts[0][1] == "listen"
        assert transcripts[0][3] == "Hello world"

        # Phase 2: ストリーミング翻訳
        partials = [m for m in messages if m[0] == "partial"]
        assert len(partials) == 1
        assert partials[0][2] == "こんにちは世界"

        # Phase 1 used generate_content, Phase 2 used generate_content_stream
        assert client.models.generate_content.call_count == 1
        assert client.models.generate_content_stream.call_count == 1

    def test_silence_does_not_trigger_phase2(self):
        """無音（SILENCE_SENTINEL）の場合は Phase 2 が送信されない"""
        phase1_response = MagicMock()
        phase1_response.text = SILENCE_SENTINEL
        client = MagicMock()
        client.models.generate_content.return_value = phase1_response

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="transcribe",
                stream_id="listen",
                phase=1,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue, timeout=1.5)
        finally:
            worker.stop()

        # 無音 → transcript も partial も出ない
        assert len(messages) == 0
        # Phase 1 used generate_content
        assert client.models.generate_content.call_count == 1
        # Phase 2 should not have been triggered
        assert client.models.generate_content_stream.call_count == 0

    def test_empty_transcript_does_not_trigger_phase2(self):
        """空の文字起こし結果では Phase 2 が送信されない"""
        phase1_response = MagicMock()
        phase1_response.text = "   "
        client = MagicMock()
        client.models.generate_content.return_value = phase1_response

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100,
                prompt="transcribe",
                stream_id="listen",
                phase=1,
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue, timeout=1.5)
        finally:
            worker.stop()

        assert len(messages) == 0
        assert client.models.generate_content.call_count == 1


class TestStrategyControllerIntegration:
    """Strategy + Controller 統合テスト"""

    def test_continuous_strategy_triggers_callback(self):
        """ContinuousStrategy → callback → controller.on_audio_chunk → worker.submit"""
        import math
        import struct
        from unittest.mock import patch as _patch
        from realtime_translator.record_strategies import ContinuousStrategy
        from realtime_translator.controller import TranslatorController, StartConfig
        from realtime_translator.constants import AUDIO_CHUNK_SIZE

        # Loud PCM frame
        n = AUDIO_CHUNK_SIZE
        samples = [int(10000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n)]
        loud_frame = struct.pack(f"<{n}h", *samples)

        ui_queue = queue.Queue()
        submitted = []

        class FakeWorker:
            def __init__(self, *a, **kw): self._label = kw.get("label", "")
            def start(self): pass
            def signal_stop(self): pass
            def join(self, timeout=10): pass
            def submit(self, req): submitted.append(req)
            @property
            def is_running(self): return True

        class FakeCapture:
            def __init__(self, *a, **kw):
                self.cb = a[2]  # callback is 3rd positional arg
            def start(self): pass
            def signal_stop(self): pass
            def join(self, timeout=3): pass

        with _patch("realtime_translator.controller.GENAI_AVAILABLE", True):
            ctrl = TranslatorController(
                ui_queue,
                capture_factory=FakeCapture,
                api_worker_factory=FakeWorker,
                client_factory=lambda k: None,
            )
            config = StartConfig(
                api_key="AI" + "x" * 37, context="test", chunk_seconds=5,
                enable_listen=True, enable_speak=False,
                loopback_device_index=0, mic_device_index=None,
                ptt_enabled=False, use_vad=False,
                request_whisper=False, request_two_phase=False,
            )
            ctrl.start(config)

        # Simulate strategy producing WAV via callback
        strategy = ContinuousStrategy(
            frames_needed=AUDIO_CHUNK_SIZE, channels=1,
            sample_rate=16000, silence_threshold=200,
        )
        wav = strategy.process_frame(loud_frame)
        assert wav is not None

        # Feed WAV through controller callback (same path as real capture)
        ctrl._capture_listen.cb(wav)
        assert len(submitted) == 1
        assert submitted[0].stream_id == "listen_en_ja"
        assert submitted[0].phase == 0
        ctrl.stop()

    def test_ptt_strategy_end_to_end(self):
        """PTTStrategy press→release → controller receives chunk"""
        import math
        import struct
        import threading as _threading
        from unittest.mock import patch as _patch
        from realtime_translator.record_strategies import PTTStrategy
        from realtime_translator.controller import TranslatorController, StartConfig
        from realtime_translator.constants import AUDIO_CHUNK_SIZE

        n = AUDIO_CHUNK_SIZE
        samples = [int(10000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n)]
        loud_frame = struct.pack(f"<{n}h", *samples)

        ui_queue = queue.Queue()
        submitted = []

        class FakeWorker:
            def __init__(self, *a, **kw): self._label = kw.get("label", "")
            def start(self): pass
            def signal_stop(self): pass
            def join(self, timeout=10): pass
            def submit(self, req): submitted.append(req)
            @property
            def is_running(self): return True

        class FakeCapture:
            def __init__(self, *a, **kw):
                self.cb = a[2]
                self.ptt_event = kw.get("ptt_event")
            def start(self): pass
            def signal_stop(self): pass
            def join(self, timeout=3): pass

        with _patch("realtime_translator.controller.GENAI_AVAILABLE", True):
            ctrl = TranslatorController(
                ui_queue,
                capture_factory=FakeCapture,
                api_worker_factory=FakeWorker,
                client_factory=lambda k: None,
            )
            config = StartConfig(
                api_key="AI" + "x" * 37, context="test", chunk_seconds=5,
                enable_listen=False, enable_speak=True,
                loopback_device_index=None, mic_device_index=1,
                ptt_enabled=True, use_vad=False,
                request_whisper=False, request_two_phase=False,
            )
            ctrl.start(config)

        ptt_ev = ctrl._capture_speak.ptt_event
        assert ptt_ev is not None

        strategy = PTTStrategy(ptt_ev, 1, 16000, 200)

        # PTT press
        ctrl.ptt_press()
        assert ptt_ev.is_set()
        assert strategy.process_frame(loud_frame) is None  # accumulating

        # PTT release
        ctrl.ptt_release()
        wav = strategy.process_frame(loud_frame)
        assert wav is not None

        # Feed through callback
        ctrl._capture_speak.cb(wav)
        assert len(submitted) == 1
        assert submitted[0].stream_id == "speak_ja_en"
        ctrl.stop()


class TestWorkerLifecycle:
    """ワーカーのライフサイクルテスト"""

    def test_submit_after_stop_is_ignored(self):
        """停止後の submit は無視される"""
        client = _make_mock_client([])
        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        worker.stop()

        req = ApiRequest(wav_bytes=b"\x00", prompt="test", stream_id="listen")
        worker.submit(req)  # should not raise
        assert ui_queue.empty()

    def test_worker_not_running_before_start(self):
        """start() 前は is_running == False"""
        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, min_interval_sec=0.0)
        assert worker.is_running is False


# ─────────────────────── Cross-backend integration tests ───────────────────────

def _make_openai_streaming_chunk(content):
    """OpenAI形式のストリーミングチャンクを生成"""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


def _make_openai_client(chunks_list):
    """OpenAI Chat Completions モッククライアント"""
    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks_list)
    return client


class TestOpenAiLlmIntegration:
    """OpenAiLlmWorker の統合テスト（ストリーミングパイプライン）"""

    def test_openai_llm_streaming_pipeline(self):
        """OpenAiLlmWorker: submit → streaming → partial_start/partial/partial_end"""
        from realtime_translator.openai_llm import OpenAiLlmWorker

        chunks = [_make_openai_streaming_chunk("こんにちは"), _make_openai_streaming_chunk("世界")]
        client = _make_openai_client(chunks)

        ui_queue = queue.Queue()
        worker = OpenAiLlmWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=None, prompt="translate: Hello world",
                stream_id="listen", phase=2, transcript="Hello world",
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        types = [m[0] for m in messages]
        assert "partial_start" in types
        assert "partial_end" in types
        partials = [m[2] for m in messages if m[0] == "partial"]
        assert "".join(partials) == "こんにちは世界"

    def test_openai_llm_phase1_triggers_phase2(self):
        """OpenAiLlmWorker: Phase1 STT (non-streaming) → transcript + Phase2 自動投入"""
        from realtime_translator.openai_llm import OpenAiLlmWorker

        # Phase 1: non-streaming response
        phase1_response = MagicMock()
        phase1_message = MagicMock()
        phase1_message.content = "Hello world"
        phase1_choice = MagicMock()
        phase1_choice.message = phase1_message
        phase1_response.choices = [phase1_choice]

        # Phase 2: streaming
        phase2_chunks = [_make_openai_streaming_chunk("こんにちは世界")]

        call_count = 0
        def create_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return phase1_response
            else:
                return iter(phase2_chunks)

        client = MagicMock()
        client.chat.completions.create.side_effect = create_side_effect

        ui_queue = queue.Queue()
        worker = OpenAiLlmWorker(
            ui_queue, client=client, min_interval_sec=0.0,
            model="gpt-4o-audio-preview",
        )
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100, prompt="transcribe",
                stream_id="listen", phase=1, context="meeting",
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue, timeout=3.0)
        finally:
            worker.stop()

        transcripts = [m for m in messages if m[0] == "transcript"]
        assert len(transcripts) == 1
        assert transcripts[0][3] == "Hello world"

        partials = [m for m in messages if m[0] == "partial"]
        assert len(partials) >= 1

        assert client.chat.completions.create.call_count == 2


class TestOpenAiSttIntegration:
    """OpenAiSttWorker → LLMワーカー パイプライン統合テスト"""

    def test_stt_to_llm_pipeline(self):
        """OpenAiSttWorker → transcript → Phase2 submit to LLMワーカー"""
        from realtime_translator.openai_stt import OpenAiSttWorker

        # Mock STT client
        stt_client = MagicMock()
        stt_response = MagicMock()
        stt_response.text = "Hello world"
        stt_client.audio.transcriptions.create.return_value = stt_response

        # Mock LLM worker (receives phase=2)
        submitted = []

        class FakeLlmWorker:
            @property
            def is_running(self):
                return True
            def submit(self, req):
                submitted.append(req)

        ui_queue = queue.Queue()
        stt_worker = OpenAiSttWorker(
            api_worker_listen=FakeLlmWorker(),
            api_worker_speak=FakeLlmWorker(),
            ui_queue=ui_queue,
            client=stt_client,
            context="test meeting",
        )
        stt_worker.start()
        try:
            stt_worker.submit(b"\x00" * 100, "listen")
            time.sleep(0.5)
        finally:
            stt_worker.stop()

        # transcript がUIキューに入っている
        messages = []
        while not ui_queue.empty():
            messages.append(ui_queue.get_nowait())
        transcripts = [m for m in messages if m[0] == "transcript"]
        assert len(transcripts) == 1
        assert transcripts[0][3] == "Hello world"

        # Phase2リクエストがLLMワーカーに投入された
        assert len(submitted) == 1
        assert submitted[0].phase == 2
        assert submitted[0].stream_id == "listen"
        assert submitted[0].transcript == "Hello world"

    def test_stt_error_propagation(self):
        """OpenAiSttWorker: API例外 → error メッセージがUIキューに入る"""
        from realtime_translator.openai_stt import OpenAiSttWorker

        stt_client = MagicMock()
        stt_client.audio.transcriptions.create.side_effect = RuntimeError("transcription failed")

        class FakeLlmWorker:
            @property
            def is_running(self):
                return True
            def submit(self, req):
                pass

        ui_queue = queue.Queue()
        stt_worker = OpenAiSttWorker(
            api_worker_listen=FakeLlmWorker(),
            api_worker_speak=FakeLlmWorker(),
            ui_queue=ui_queue,
            client=stt_client,
            context="test",
        )
        stt_worker.start()
        try:
            stt_worker.submit(b"\x00" * 100, "listen")
            time.sleep(0.5)
        finally:
            stt_worker.stop()

        messages = []
        while not ui_queue.empty():
            messages.append(ui_queue.get_nowait())
        errors = [m for m in messages if m[0] == "error"]
        assert len(errors) == 1
        assert errors[0][1] == "listen"


class TestGeminiModelSelection:
    """Geminiモデル選択の統合テスト"""

    def test_custom_model_used_in_api_call(self):
        """ApiWorker にカスタムモデルを渡すと generate_content_stream に反映される"""
        chunks = [_FakeChunk("translated")]
        client = _make_mock_client(chunks)

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0, model="gemini-2.0-flash")
        worker.start()
        try:
            req = ApiRequest(wav_bytes=None, prompt="translate", stream_id="listen", phase=2)
            worker.submit(req)
            _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        call_kwargs = client.models.generate_content_stream.call_args
        assert call_kwargs.kwargs.get("model") == "gemini-2.0-flash"

    def test_25_model_gets_thinking_config(self):
        """2.5系モデルは ThinkingConfig が適用される"""
        from realtime_translator.api import _generate_config_for_model, _THINKING_CONFIG
        config = _generate_config_for_model("gemini-2.5-flash")
        if _THINKING_CONFIG is not None:
            assert config is _THINKING_CONFIG
        # 2.5でないモデルはNone
        assert _generate_config_for_model("gemini-2.0-flash") is None

    def test_non_25_model_no_thinking_config(self):
        """2.5以外のモデルは ThinkingConfig なし"""
        chunks = [_FakeChunk("result")]
        client = _make_mock_client(chunks)

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0, model="gemini-2.0-flash")
        worker.start()
        try:
            req = ApiRequest(wav_bytes=None, prompt="translate", stream_id="speak", phase=2)
            worker.submit(req)
            _drain_ui_queue(ui_queue)
        finally:
            worker.stop()

        call_kwargs = client.models.generate_content_stream.call_args
        assert call_kwargs.kwargs.get("config") is None


class TestCrossBackendControllerIntegration:
    """Controller経由のクロスバックエンド統合テスト"""

    def test_openai_llm_backend_full_pipeline(self):
        """Controller(llm_backend=openai) → OpenAiLlmWorker経由でUIキューに到達"""
        from unittest.mock import patch as _patch
        from realtime_translator.controller import TranslatorController, StartConfig
        from realtime_translator.openai_llm import OpenAiLlmWorker

        chunks = [_make_openai_streaming_chunk("翻訳結果")]
        openai_client = _make_openai_client(chunks)

        ui_queue = queue.Queue()

        class FakeCapture:
            def __init__(self, *a, **kw):
                self.cb = a[2]
            def start(self): pass
            def signal_stop(self): pass
            def join(self, timeout=3): pass

        with _patch("realtime_translator.controller.GENAI_AVAILABLE", True), \
             _patch("realtime_translator.controller.OPENAI_AVAILABLE", True):
            ctrl = TranslatorController(
                ui_queue,
                capture_factory=FakeCapture,
                openai_client_factory=lambda key, base_url=None: openai_client,
            )
            config = StartConfig(
                api_key="AI" + "x" * 37, context="test", chunk_seconds=5,
                enable_listen=True, enable_speak=False,
                loopback_device_index=0, mic_device_index=None,
                ptt_enabled=False, use_vad=False,
                request_whisper=False, request_two_phase=False,
                llm_backend="openai", openai_api_key="sk-test",
                # audio-capable model needed for phase=0 with wav_bytes
                openai_chat_model="gpt-4o-audio-preview",
            )
            ctrl.start(config)

        # Simulate audio chunk via callback
        ctrl._capture_listen.cb(b"\x00" * 100)  # triggers on_audio_chunk → submit
        messages = _drain_ui_queue(ui_queue, timeout=3.0)
        ctrl.stop()

        # OpenAI LLMワーカーがストリーミング結果を返している
        types = [m[0] for m in messages]
        assert "partial_start" in types
        assert "partial_end" in types
        partials = [m[2] for m in messages if m[0] == "partial"]
        assert "翻訳結果" in "".join(partials)

    def test_openai_stt_plus_gemini_llm_pipeline(self):
        """Controller(stt=openai, llm=gemini) → OpenAiStt → transcript → ApiWorker"""
        from unittest.mock import patch as _patch
        from realtime_translator.controller import TranslatorController, StartConfig

        # Mock OpenAI STT client
        stt_client = MagicMock()
        stt_response = MagicMock()
        stt_response.text = "Hello world"
        stt_client.audio.transcriptions.create.return_value = stt_response

        # Mock Gemini LLM client
        gemini_client = _make_mock_client([_FakeChunk("こんにちは世界")])

        ui_queue = queue.Queue()

        class FakeCapture:
            def __init__(self, *a, **kw):
                self.cb = a[2]
            def start(self): pass
            def signal_stop(self): pass
            def join(self, timeout=3): pass

        with _patch("realtime_translator.controller.GENAI_AVAILABLE", True), \
             _patch("realtime_translator.controller.OPENAI_AVAILABLE", True):
            ctrl = TranslatorController(
                ui_queue,
                capture_factory=FakeCapture,
                client_factory=lambda key: gemini_client,
                openai_client_factory=lambda key, base_url=None: stt_client,
            )
            config = StartConfig(
                api_key="AI" + "x" * 37, context="test", chunk_seconds=5,
                enable_listen=True, enable_speak=False,
                loopback_device_index=0, mic_device_index=None,
                ptt_enabled=False, use_vad=False,
                request_whisper=False, request_two_phase=False,
                stt_backend="openai", llm_backend="gemini",
                openai_api_key="sk-test",
            )
            ctrl.start(config)

        # Simulate audio chunk → OpenAI STT → transcript
        ctrl._capture_listen.cb(b"\x00" * 100)
        messages = _drain_ui_queue(ui_queue, timeout=4.0)
        ctrl.stop()

        # STTの文字起こし結果がUIキューに入る
        transcripts = [m for m in messages if m[0] == "transcript"]
        assert len(transcripts) == 1
        assert transcripts[0][3] == "Hello world"

        # Gemini LLMの翻訳結果もUIキューに入る
        partials = [m for m in messages if m[0] == "partial"]
        assert len(partials) >= 1
        assert "こんにちは世界" in "".join(m[2] for m in partials)


class TestShowOriginal:
    """show_original flag regression tests."""

    def test_two_phase_translation_done_preserves_original_when_show_original_false(self):
        """Controller-level: translation_done event contains original text regardless of show_original."""
        phase1_response = MagicMock()
        phase1_response.text = "Hello world"
        client = MagicMock()
        client.models.generate_content.return_value = phase1_response
        client.models.generate_content_stream.return_value = iter([_FakeChunk("こんにちは世界")])

        ui_queue = queue.Queue()
        worker = ApiWorker(ui_queue, client=client, min_interval_sec=0.0)
        worker.start()
        try:
            req = ApiRequest(
                wav_bytes=b"\x00" * 100, prompt="transcribe",
                stream_id="listen", phase=1, context="meeting",
            )
            worker.submit(req)
            messages = _drain_ui_queue(ui_queue, timeout=3.0)
        finally:
            worker.stop()

        done_msgs = [m for m in messages if m[0] == "translation_done"]
        assert len(done_msgs) == 1
        assert done_msgs[0][3] == "Hello world"
        assert done_msgs[0][4] == "こんにちは世界"

    def test_on_transcript_skips_insert_when_show_original_false(self):
        """UI-level: _on_transcript should not insert text when show_original=False."""
        from contextlib import contextmanager
        from realtime_translator.app import TranslatorApp

        app = object.__new__(TranslatorApp)
        show_var = MagicMock()
        show_var.get.return_value = False
        app._show_original_var = show_var
        result_text = MagicMock()
        app._result_text = result_text
        app._flush_active_partials = MagicMock()
        app._stream_buffers = {}

        TranslatorApp._on_transcript(app, "listen", "12:00:00", "Hello world")

        app._flush_active_partials.assert_not_called()
        result_text.insert.assert_not_called()

    def test_on_transcript_inserts_when_show_original_true(self):
        """UI-level: _on_transcript should insert text when show_original=True."""
        from contextlib import contextmanager
        from realtime_translator.app import TranslatorApp

        app = object.__new__(TranslatorApp)
        show_var = MagicMock()
        show_var.get.return_value = True
        app._show_original_var = show_var
        result_text = MagicMock()
        app._result_text = result_text
        app._flush_active_partials = MagicMock()
        app._stream_buffers = {}

        @contextmanager
        def fake_editable():
            yield
        app._editable_result = fake_editable

        TranslatorApp._on_transcript(app, "listen", "12:00:00", "Hello world")

        app._flush_active_partials.assert_called_once()
        assert result_text.insert.call_count >= 2


class TestAnnotationIntegration:
    def test_translation_done_legacy_tuple_annotates_with_fixed_output_language(self):
        from realtime_translator.app import TranslatorApp

        app = _make_poll_queue_app()
        with patch(
            "realtime_translator.app.annotate_translation",
            return_value="12 mm (0.47 in, twelve millimeters)",
        ) as annotate:
            app._ui_queue.put(("translation_done", "listen_en_ja", "12:00:00", "12 mm", "12 mm"))
            TranslatorApp._poll_queue(app)

        annotate.assert_called_once_with("12 mm", output_language="ja")
        entry = app._controller.history.all_entries()[-1]
        assert entry.original == "12 mm"
        assert entry.translation == "12 mm (0.47 in, twelve millimeters)"
        assert entry.virtual_stream_id == "listen_en_ja"
        assert entry.resolved_direction == "en_ja"
        assert entry.error is None

    def test_translation_done_auto_tuple_uses_resolved_direction_for_output_language(self):
        from realtime_translator.app import TranslatorApp

        app = _make_poll_queue_app()
        with patch(
            "realtime_translator.app.annotate_translation",
            return_value="35 psi (0.24 MPa / 2.41 bar, thirty-five psi)",
        ) as annotate:
            app._ui_queue.put(
                ("translation_done", "listen", "listen_auto", "ja_en", "12:00:01", "35 psi", "35 psi", None)
            )
            TranslatorApp._poll_queue(app)

        annotate.assert_called_once_with("35 psi", output_language="en")
        entry = app._controller.history.all_entries()[-1]
        assert entry.translation == "35 psi (0.24 MPa / 2.41 bar, thirty-five psi)"
        assert entry.virtual_stream_id == "listen_auto"
        assert entry.resolved_direction == "ja_en"
        assert entry.error is None

    def test_translation_done_annotation_failure_falls_back_to_raw(self):
        from realtime_translator.app import TranslatorApp

        app = _make_poll_queue_app()
        with patch("realtime_translator.app.annotate_translation", side_effect=RuntimeError("boom")):
            app._ui_queue.put(("translation_done", "listen_en_ja", "12:00:00", "12 mm", "12 mm"))
            TranslatorApp._poll_queue(app)

        entry = app._controller.history.all_entries()[-1]
        assert entry.translation == "12 mm"

    def test_transcript_event_is_not_annotated_and_does_not_append_history(self):
        from realtime_translator.app import TranslatorApp

        app = _make_poll_queue_app()
        app._on_transcript = MagicMock()
        with patch("realtime_translator.app.annotate_translation") as annotate:
            app._ui_queue.put(("transcript", "listen_en_ja", "12:00:00", "12 mm"))
            TranslatorApp._poll_queue(app)

        annotate.assert_not_called()
        app._on_transcript.assert_called_once_with("listen_en_ja", "12:00:00", "12 mm")
        assert app._controller.history.all_entries() == []

    def test_assist_result_routes_to_on_assist_result_without_annotation(self):
        from realtime_translator.app import TranslatorApp

        app = _make_poll_queue_app()
        with patch("realtime_translator.app.annotate_translation") as annotate:
            app._ui_queue.put(("assist_result", "req1", "reply_assist", "hello"))
            TranslatorApp._poll_queue(app)

        annotate.assert_not_called()
        app._tools_panel.on_assist_result.assert_called_once_with("req1", "hello")

    def test_minutes_result_routes_to_on_minutes_result_without_annotation(self):
        from realtime_translator.app import TranslatorApp

        app = _make_poll_queue_app()
        with patch("realtime_translator.app.annotate_translation") as annotate:
            app._ui_queue.put(("assist_result", "req2", "minutes", "hello"))
            TranslatorApp._poll_queue(app)

        annotate.assert_not_called()
        app._tools_panel.on_minutes_result.assert_called_once_with("req2", "hello")

    def test_output_language_resolution_fallback_is_deterministic(self):
        from realtime_translator.app import TranslatorApp

        assert (
            TranslatorApp._resolve_output_language(
                virtual_stream_id="listen_auto",
                resolved_direction="unexpected",
            )
            == "ja"
        )


class TestStreamHeaderFormatting:
    def test_partial_header_uses_pending_auto_label_until_direction_resolves(self):
        from realtime_translator.app import format_stream_header

        assert format_stream_header("listen", "listen_auto", None) == "PC音声 同時翻訳"

    def test_resolved_header_for_speak_ja_en(self):
        from realtime_translator.app import format_stream_header

        assert format_stream_header("speak", "speak_ja_en", "ja_en") == "マイク 日本語→英語"


class TestMainWindowLayout:
    def test_main_window_uses_three_region_layout(self):
        import tkinter as tk
        from realtime_translator.app import TranslatorApp

        python_dir = os.path.dirname(sys.executable)
        os.environ["TCL_LIBRARY"] = os.path.join(python_dir, "tcl", "tcl8.6")
        os.environ["TK_LIBRARY"] = os.path.join(python_dir, "tcl", "tk8.6")

        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(TranslatorApp, "_poll_queue", lambda self: None), \
                 patch.object(TranslatorApp, "_deferred_init", lambda self: None), \
                 patch("realtime_translator.app.load_config", return_value={}):
                app = TranslatorApp(root)
            assert app._main_controls_panel is not None
            assert app._timeline_panel is not None
            assert getattr(app, "_workspace_panel", None) is not None
        finally:
            root.destroy()

    def test_ptt_keybindings_survive_layout_swap(self):
        import tkinter as tk
        from realtime_translator.app import TranslatorApp
        from realtime_translator.constants import _PTT_BINDINGS

        python_dir = os.path.dirname(sys.executable)
        os.environ["TCL_LIBRARY"] = os.path.join(python_dir, "tcl", "tcl8.6")
        os.environ["TK_LIBRARY"] = os.path.join(python_dir, "tcl", "tk8.6")

        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(TranslatorApp, "_poll_queue", lambda self: None), \
                 patch.object(TranslatorApp, "_deferred_init", lambda self: None), \
                 patch("realtime_translator.app.load_config", return_value={}):
                app = TranslatorApp(root)

            app._api_key_var.set("AI" + "x" * 37)
            app._enable_listen_var.set(False)
            app._enable_speak_var.set(True)
            app._ptt_var.set(True)
            app._mic_var.set("Mic A")
            app._mic_devices = [{"name": "Mic A", "index": 1}]
            app._loopback_devices = []

            app._controller.start = MagicMock()
            app._controller.stop = MagicMock()
            app._controller._use_whisper = False
            app._controller._use_two_phase = False

            app._start_inner()
            assert all(root.bind(event) for event in _PTT_BINDINGS)

            app._stop()
            assert all(not root.bind(event) for event in _PTT_BINDINGS)
        finally:
            root.destroy()
