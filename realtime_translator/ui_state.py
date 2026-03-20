"""UI contract helpers for the main window."""

from dataclasses import dataclass
from typing import Literal

UiScope = Literal["session", "tool"]
UiSeverity = Literal["blocker", "runtime"]
GlobalStatusKind = Literal["error", "ptt", "running", "initializing", "idle"]


@dataclass(frozen=True)
class UiError:
    scope: UiScope
    severity: UiSeverity
    source: str
    message: str
    stream_id: str | None = None


@dataclass(frozen=True)
class SessionSummary:
    listen_enabled: bool
    speak_enabled: bool
    pc_audio_label: str
    mic_label: str
    mode_summary: tuple[str, ...] = ()
    device_summary: tuple[str, str] = ("PC音声デバイス: 未取得", "マイクデバイス: 未取得")
    backend_summary: str = "STT: 未設定 / 翻訳: 未設定"
    config_updated_at: str = "未更新"

    @property
    def active_stream_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.listen_enabled:
            labels.append("聴く")
        if self.speak_enabled:
            labels.append("話す")
        return tuple(labels)

    @property
    def mode_summary_text(self) -> str:
        return " / ".join(self.mode_summary) if self.mode_summary else "通常"

    @property
    def configuration_lines(self) -> tuple[str, ...]:
        return (
            self.pc_audio_label,
            self.mic_label,
            *self.device_summary,
            self.backend_summary,
            f"構成更新: {self.config_updated_at}",
        )


@dataclass(frozen=True)
class GlobalStatus:
    kind: GlobalStatusKind
    message: str


class GlobalStatusResolver:
    """Resolve one status string from concurrent session flags."""

    def resolve(
        self,
        *,
        session_error: str | None,
        ptt_recording: bool,
        running: bool,
        initializing: bool,
        runtime_status_message: str | None = None,
    ) -> GlobalStatus:
        if session_error:
            return GlobalStatus("error", session_error)
        if ptt_recording:
            return GlobalStatus("ptt", "🎙 録音中 (Space/ボタンを離すと送信)")
        if running:
            return GlobalStatus("running", runtime_status_message or "翻訳中...")
        if initializing:
            return GlobalStatus("initializing", runtime_status_message or "初期化中...")
        return GlobalStatus("idle", runtime_status_message or "待機中")


def normalize_ui_error(
    event: UiError | tuple | str,
    *,
    source_hint: str,
    scope_hint: UiScope = "session",
    severity_hint: UiSeverity = "runtime",
) -> UiError:
    if isinstance(event, UiError):
        return event

    stream_id: str | None = None
    message: str

    if isinstance(event, tuple):
        if len(event) < 3 or event[0] != "error":
            raise ValueError(f"Unsupported legacy error event: {event!r}")
        _, stream_id, message = event[:3]
    else:
        message = str(event)

    return UiError(
        scope=scope_hint,
        severity=severity_hint,
        source=source_hint,
        message=message,
        stream_id=stream_id,
    )
