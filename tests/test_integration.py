"""ApiWorker 統合テスト: audio-chunk -> ApiWorker -> UI-queue パイプライン"""
import queue
import time
from unittest.mock import MagicMock, patch

import pytest

import realtime_translator.api as api_module
from realtime_translator.api import ApiWorker, ApiRequest
from realtime_translator.constants import SILENCE_SENTINEL

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
        # 最後は partial_end
        assert messages[-1][0] == "partial_end"
        assert messages[-1][1] == "listen"

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
        assert messages[0][0] == "partial_start"
        assert messages[-1][0] == "partial_end"


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
        call_count = 0

        def stream_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Phase 1: 文字起こし結果
                return iter([_FakeChunk("Hello world")])
            else:
                # Phase 2: 翻訳結果
                return iter([_FakeChunk("こんにちは世界")])

        client = MagicMock()
        client.models.generate_content_stream.side_effect = stream_side_effect

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

        # API が2回呼ばれた（Phase1 + Phase2）
        assert client.models.generate_content_stream.call_count == 2

    def test_silence_does_not_trigger_phase2(self):
        """無音（SILENCE_SENTINEL）の場合は Phase 2 が送信されない"""
        client = _make_mock_client([_FakeChunk(SILENCE_SENTINEL)])

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
        # API は1回だけ（Phase 1 のみ）
        assert client.models.generate_content_stream.call_count == 1

    def test_empty_transcript_does_not_trigger_phase2(self):
        """空の文字起こし結果では Phase 2 が送信されない"""
        client = _make_mock_client([_FakeChunk("   ")])

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
        assert client.models.generate_content_stream.call_count == 1


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
