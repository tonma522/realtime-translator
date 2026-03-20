"""UI state helpers contract tests."""

from realtime_translator.ui_state import (
    GlobalStatusResolver,
    SessionSummary,
    normalize_ui_error,
)


def test_normalize_legacy_translation_error_to_session_runtime():
    event = ("error", "listen", "API limit exceeded")

    normalized = normalize_ui_error(event, source_hint="translation")

    assert normalized.scope == "session"
    assert normalized.severity == "runtime"
    assert normalized.source == "translation"
    assert normalized.stream_id == "listen"
    assert normalized.message == "API limit exceeded"


def test_global_status_resolver_prefers_error_over_ptt():
    resolver = GlobalStatusResolver()

    status = resolver.resolve(
        session_error="起動エラー",
        ptt_recording=True,
        running=True,
        initializing=False,
    )

    assert status.kind == "error"
    assert status.message == "起動エラー"


def test_normalize_startup_message_to_session_blocker():
    normalized = normalize_ui_error(
        "Gemini APIキーが未設定です",
        source_hint="startup",
        severity_hint="blocker",
    )

    assert normalized.scope == "session"
    assert normalized.severity == "blocker"
    assert normalized.source == "startup"
    assert normalized.stream_id is None


def test_session_summary_formats_mode_summary():
    summary = SessionSummary(
        listen_enabled=True,
        speak_enabled=False,
        pc_audio_label="PC音声: 英語→日本語",
        mic_label="マイク: 日本語→英語",
        mode_summary=("PTT", "原文表示"),
        device_summary=("PC音声デバイス: Speakers", "マイクデバイス: Yeti"),
        backend_summary="STT: Gemini / 翻訳: Gemini",
        config_updated_at="15:42:18",
    )

    assert summary.active_stream_labels == ("聴く",)
    assert summary.mode_summary_text == "PTT / 原文表示"
    assert summary.configuration_lines == (
        "PC音声: 英語→日本語",
        "マイク: 日本語→英語",
        "PC音声デバイス: Speakers",
        "マイクデバイス: Yeti",
        "STT: Gemini / 翻訳: Gemini",
        "構成更新: 15:42:18",
    )
