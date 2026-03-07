"""双方向リアルタイム音声翻訳ツール"""
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import threading
import queue
import json
import wave
import io
import math
import struct
import time
import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    pyaudio = None
    PYAUDIO_AVAILABLE = False

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
    GENAI_AVAILABLE = False

try:
    import webrtcvad as _webrtcvad
    WEBRTCVAD_AVAILABLE = True
except ImportError:
    _webrtcvad = None
    WEBRTCVAD_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    WHISPER_AVAILABLE = False

CONFIG_PATH = Path.home() / ".realtime_translator_config.json"
LOG_PATH = Path(__file__).parent / "realtime_translator.log"
GEMINI_MODEL = "gemini-2.5-flash"
MIN_API_INTERVAL_SEC = 4.0   # Free tier 15RPM
API_QUEUE_MAXSIZE = 3
AUDIO_CHUNK_SIZE = 1024
SILENCE_RMS_THRESHOLD = 200      # ループバック向け
MIC_SILENCE_RMS_THRESHOLD = 500  # マイク誤検知防止

_PTT_BINDINGS = ("<KeyPress-space>", "<KeyRelease-space>", "<FocusOut>")
_STREAM_META: dict[str, tuple[str, str]] = {
    "listen": ("PC音声→日本語", "stream_listen"),
    "speak":  ("マイク→英語",   "stream_speak"),
}


# ─────────────────────────── デバイス列挙 ───────────────────────────

def _enum_devices(loopback: bool, pa=None) -> list[dict]:
    if not PYAUDIO_AVAILABLE:
        return []
    own = pa is None
    if own:
        pa = pyaudio.PyAudio()
    devices = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            is_lb = bool(info.get("isLoopbackDevice", False))
            match = is_lb if loopback else (int(info.get("maxInputChannels", 0)) > 0 and not is_lb)
            if match:
                devices.append({"index": i, "name": info["name"]})
    finally:
        if own:
            pa.terminate()
    return devices


# ─────────────────────────── VAD ───────────────────────────

class VoiceActivityDetector:
    """webrtcvad ベースの発話区間検出。非対応時は RMS フォールバック"""
    FRAME_MS = 30  # webrtcvad は 10/20/30ms フレームのみ対応

    def __init__(self, sample_rate: int, aggressiveness: int = 2):
        self._sr = sample_rate
        self._frame_bytes = int(sample_rate * self.FRAME_MS / 1000) * 2
        if WEBRTCVAD_AVAILABLE and sample_rate in (8000, 16000, 32000, 48000):
            self._vad = _webrtcvad.Vad(aggressiveness)
        else:
            self._vad = None  # 44.1kHz 等は RMS フォールバック

    def is_speech(self, pcm_bytes: bytes) -> bool:
        if self._vad is None:
            return not AudioCapture._is_silent_pcm([pcm_bytes])
        frame = pcm_bytes[:self._frame_bytes]
        if len(frame) < self._frame_bytes:
            return False
        return self._vad.is_speech(frame, self._sr)


# ─────────────────────────── 音声キャプチャ ───────────────────────────

class AudioCapture:
    """loopback / マイク両対応の汎用音声キャプチャクラス"""

    def __init__(self, device_index: int, chunk_seconds: int, callback,
                 label: str = "audio", pa=None,
                 ptt_event: threading.Event | None = None,
                 use_vad: bool = False,
                 silence_threshold: int = SILENCE_RMS_THRESHOLD):
        self.device_index = device_index
        self.chunk_seconds = chunk_seconds
        self.callback = callback
        self.label = label
        self._pa = pa
        self._ptt_event = ptt_event
        self._use_vad = use_vad
        self._silence_threshold = silence_threshold
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._record_loop, name=f"AudioCapture-{self.label}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running and self._thread is None:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _record_loop(self) -> None:
        own_pa = self._pa is None
        pa = pyaudio.PyAudio() if own_pa else self._pa
        try:
            info = pa.get_device_info_by_index(self.device_index)
            sample_rate = int(info["defaultSampleRate"])
            channels = int(info.get("maxInputChannels", 0)) or int(info.get("maxOutputChannels", 0)) or 2
            frames_needed = sample_rate * self.chunk_seconds
            stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                input=True, input_device_index=self.device_index,
                frames_per_buffer=AUDIO_CHUNK_SIZE,
            )

            frames: list[bytes] = []
            total_frames = 0
            was_ptt_active = False

            # VAD用 (PTTモードでは使わない)
            use_vad_mode = self._use_vad and self._ptt_event is None
            vad = VoiceActivityDetector(sample_rate) if use_vad_mode else None
            speech_frames: list[bytes] = []
            silent_count = 0
            silence_trigger = max(1, int(sample_rate * 0.8 / AUDIO_CHUNK_SIZE))
            max_speech_chunks = int(sample_rate * self.chunk_seconds * 2 / AUDIO_CHUNK_SIZE)

            while self._running:
                try:
                    data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
                    if self._ptt_event is not None:
                        # PTTモード
                        ptt_active = self._ptt_event.is_set()
                        if ptt_active:
                            frames.append(data)
                            was_ptt_active = True
                        elif was_ptt_active:
                            if frames and not self._is_silent_pcm(frames, self._silence_threshold):
                                wav_bytes = self._to_wav(frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    pass
                            frames = []
                            was_ptt_active = False
                    elif use_vad_mode:
                        # VADモード: フレーム単位で発話検出
                        is_sp = vad.is_speech(data)
                        if is_sp:
                            speech_frames.append(data)
                            silent_count = 0
                        elif speech_frames:
                            silent_count += 1
                            speech_frames.append(data)
                            if silent_count >= silence_trigger:
                                wav_bytes = self._to_wav(speech_frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    pass
                                speech_frames = []
                                silent_count = 0
                        if len(speech_frames) >= max_speech_chunks:
                            if speech_frames:
                                wav_bytes = self._to_wav(speech_frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    pass
                                speech_frames = []
                                silent_count = 0
                    else:
                        # 連続モード: チャンク単位
                        frames.append(data)
                        total_frames += AUDIO_CHUNK_SIZE
                        if total_frames >= frames_needed:
                            if not self._is_silent_pcm(frames, self._silence_threshold):
                                wav_bytes = self._to_wav(frames, channels, sample_rate)
                                try:
                                    self.callback(wav_bytes)
                                except Exception:
                                    pass
                            frames = []
                            total_frames = 0
                except Exception:
                    break

            stream.stop_stream()
            stream.close()
        finally:
            if own_pa:
                pa.terminate()

    @staticmethod
    def _to_wav(frames: list[bytes], channels: int, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    @staticmethod
    def _is_silent_pcm(frames: list[bytes], threshold: int = SILENCE_RMS_THRESHOLD) -> bool:
        """生PCMフレームのRMS振幅がthreshold以下ならTrue"""
        pcm = b"".join(frames)
        n = len(pcm) // 2
        if n == 0:
            return True
        samples = struct.unpack(f"<{n}h", pcm[:n * 2])
        rms = math.sqrt(sum(s * s for s in samples) / n)
        logging.debug("[VAD] RMS=%.1f threshold=%d", rms, threshold)
        return rms < threshold


# ─────────────────────────── Gemini API統合 ───────────────────────────

@dataclass
class ApiRequest:
    wav_bytes: bytes | None  # phase==2 では None
    prompt: str
    stream_id: str
    phase: int = 0        # 0=通常(STT+翻訳), 1=STTのみ, 2=翻訳のみ(テキスト入力)
    context: str = ""     # phase==2 へ引き継ぐコンテキスト
    transcript: str = ""  # phase==2 の入力テキスト


class ApiWorker:
    """シリアル処理＋レート制限付きGemini APIワーカー"""

    def __init__(self, ui_queue: queue.Queue, client=None,
                 min_interval_sec: float = MIN_API_INTERVAL_SEC,
                 label: str = "ApiWorker") -> None:
        self._ui_queue = ui_queue
        self._client = client
        self._min_interval_sec = min_interval_sec
        self._label = label
        self._req_queue: queue.Queue[ApiRequest | None] = queue.Queue(maxsize=API_QUEUE_MAXSIZE)
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_call_time = 0.0

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name=self._label, daemon=True)
        self._thread.start()

    def submit(self, req: ApiRequest) -> None:
        if not self._running:
            return
        if self._req_queue.full():
            try:
                self._req_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self._req_queue.put_nowait(req)
        except queue.Full:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            self._req_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def _worker_loop(self) -> None:
        while self._running:
            try:
                req = self._req_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if req is None:
                break
            self._call_api(req)

    def _call_api(self, req: ApiRequest) -> None:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < self._min_interval_sec:
            time.sleep(self._min_interval_sec - elapsed)
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            if req.phase == 1:
                # Phase1: 音声→文字起こし（累積後 Phase2 へ）
                audio_part = genai_types.Part.from_bytes(data=req.wav_bytes, mime_type="audio/wav")
                chunks = []
                for chunk in self._client.models.generate_content_stream(
                    model=GEMINI_MODEL, contents=[req.prompt, audio_part]
                ):
                    try:
                        t = chunk.text or ""
                    except ValueError:
                        continue
                    if t:
                        chunks.append(t)
                transcript = "".join(chunks).strip()
                if transcript and "(無音)" not in transcript:
                    self._ui_queue.put(("transcript", req.stream_id, ts, transcript))
                    if self._running:
                        self.submit(ApiRequest(
                            wav_bytes=None,
                            prompt=build_translation_prompt(req.stream_id, req.context, transcript),
                            stream_id=req.stream_id, phase=2,
                            context=req.context, transcript=transcript,
                        ))
            else:
                # Phase0 (通常) または Phase2 (翻訳のみ): ストリーミング
                if req.wav_bytes is not None:
                    audio_part = genai_types.Part.from_bytes(data=req.wav_bytes, mime_type="audio/wav")
                    contents = [req.prompt, audio_part]
                else:
                    contents = [req.prompt]
                started = False
                try:
                    for chunk in self._client.models.generate_content_stream(
                        model=GEMINI_MODEL, contents=contents
                    ):
                        try:
                            text = chunk.text or ""
                        except ValueError:
                            continue
                        if not text:
                            continue
                        if not started:
                            self._ui_queue.put(("partial_start", req.stream_id, ts))
                            started = True
                        self._ui_queue.put(("partial", req.stream_id, text))
                except Exception as e:
                    self._ui_queue.put(("error", req.stream_id, str(e)))
                finally:
                    if started:
                        self._ui_queue.put(("partial_end", req.stream_id))
        except Exception as e:
            self._ui_queue.put(("error", req.stream_id, str(e)))
        finally:
            self._last_call_time = time.monotonic()


# ─────────────────────────── Whisper STT ───────────────────────────

class WhisperTranscriber:
    """faster-whisper ローカル文字起こし"""

    def __init__(self, model_size: str = "small", language: str | None = None):
        if not WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper が未インストールです (pip install faster-whisper)")
        # device="cpu" を明示（Windows CUDA DLL競合を回避）
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._language = language

    def transcribe(self, wav_bytes: bytes) -> str:
        segments, _ = self._model.transcribe(
            io.BytesIO(wav_bytes),
            language=self._language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments)


class WhisperWorker:
    """Whisper STT → ApiWorker(Phase2翻訳) パイプライン"""

    def __init__(self, api_worker_listen: "ApiWorker", api_worker_speak: "ApiWorker",
                 ui_queue: queue.Queue, model_size: str, language: str | None, context: str):
        self._api_workers = {"listen": api_worker_listen, "speak": api_worker_speak}
        self._ui_queue = ui_queue
        self._model_size = model_size
        self._language = language
        self._context = context
        self._req_queue: queue.Queue = queue.Queue(maxsize=3)
        self._running = False
        self._thread: threading.Thread | None = None
        self._transcriber: WhisperTranscriber | None = None  # スレッド内で初期化

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="WhisperWorker", daemon=True)
        self._thread.start()

    def submit(self, wav_bytes: bytes, stream_id: str) -> None:
        if not self._running:
            return
        if self._req_queue.full():
            try:
                self._req_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self._req_queue.put_nowait((wav_bytes, stream_id))
        except queue.Full:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            self._req_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=15)
            self._thread = None

    def _worker_loop(self) -> None:
        self._ui_queue.put(("status", "Whisper準備中..."))
        try:
            self._transcriber = WhisperTranscriber(self._model_size, self._language)
            self._ui_queue.put(("status", "Whisper準備完了"))
        except Exception as e:
            self._ui_queue.put(("error", "whisper", str(e)))
            self._running = False
            return

        while self._running:
            try:
                item = self._req_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            wav_bytes, stream_id = item
            ts = datetime.now().strftime("%H:%M:%S")
            try:
                transcript = self._transcriber.transcribe(wav_bytes)
                if transcript and transcript.strip():
                    self._ui_queue.put(("transcript", stream_id, ts, transcript))
                    worker = self._api_workers.get(stream_id)
                    if worker and worker._running:
                        worker.submit(ApiRequest(
                            wav_bytes=None,
                            prompt=build_translation_prompt(stream_id, self._context, transcript),
                            stream_id=stream_id, phase=2,
                            context=self._context, transcript=transcript,
                        ))
            except Exception as e:
                self._ui_queue.put(("error", stream_id, f"Whisper: {e}"))


# ─────────────────────────── プロンプト ───────────────────────────

def build_prompt(stream_id: str, context: str) -> str:
    """通常モード: 音声→STT+翻訳 (phase=0)"""
    if stream_id == "listen":
        src, dst, src_ex, dst_ex = "英語", "日本語", "英語テキスト", "日本語テキスト"
    else:
        src, dst, src_ex, dst_ex = "日本語", "英語", "日本語テキスト", "英語テキスト"
    return (
        "あなたはリアルタイム翻訳アシスタントです。\n"
        f"【コンテキスト】{context}\n\n"
        f"音声を聞き、{src}を{dst}に翻訳してください。\n"
        "出力形式（必ずこの形式で）:\n"
        f"  原文: ({src_ex})\n"
        f"  訳文: ({dst_ex})\n\n"
        "無音・聞き取れない場合は「(無音)」とだけ返してください。"
    )


def build_stt_prompt(stream_id: str) -> str:
    """2フェーズ Phase1: 音声→文字起こしのみ (phase=1)"""
    lang = "英語" if stream_id == "listen" else "日本語"
    return (
        f"この音声を{lang}でそのまま文字起こししてください。翻訳は不要です。"
        "無音・聞き取れない場合は「(無音)」とだけ返してください。"
    )


def build_translation_prompt(stream_id: str, context: str, transcript: str) -> str:
    """2フェーズ Phase2: テキスト→翻訳のみ (phase=2)"""
    src, dst = ("英語", "日本語") if stream_id == "listen" else ("日本語", "英語")
    return (
        f"【コンテキスト】{context}\n"
        f"次の{src}テキストを{dst}に翻訳してください。\n"
        f"テキスト: {transcript}\n"
        "出力形式:\n  訳文: (翻訳結果のみ)"
    )


# ─────────────────────────── tkinter UI ───────────────────────────

class TranslatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("双方向リアルタイム音声翻訳")
        self.root.resizable(True, True)

        self._ui_queue: queue.Queue = queue.Queue()
        self._api_worker_listen: ApiWorker | None = None
        self._api_worker_speak: ApiWorker | None = None
        self._whisper_worker: WhisperWorker | None = None
        self._capture_listen: AudioCapture | None = None
        self._capture_speak: AudioCapture | None = None
        self._stream_buffers: dict[str, dict] = {}  # ストリーミング状態
        self._loopback_devices: list[dict] = []
        self._mic_devices: list[dict] = []
        self._saved_loopback_name: str = ""
        self._saved_mic_name: str = ""
        self._running = False
        self._ptt_event = threading.Event()
        self._pa = pyaudio.PyAudio() if PYAUDIO_AVAILABLE else None

        self._build_ui()
        self._load_config()
        self._refresh_devices()
        self._poll_queue()

    # ─────────────────────────── UI構築 ───────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ── API設定 ──
        api_frame = ttk.LabelFrame(self.root, text="API設定")
        api_frame.pack(fill="x", **pad)
        ttk.Label(api_frame, text="Gemini APIキー:").grid(row=0, column=0, sticky="w", **pad)
        self._api_key_var = tk.StringVar()
        self._api_entry = ttk.Entry(api_frame, textvariable=self._api_key_var, show="*", width=45)
        self._api_entry.grid(row=0, column=1, sticky="ew", **pad)
        api_frame.columnconfigure(1, weight=1)

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
            # PTT ON のとき VAD は無効化
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
        self._loopback_devices = _enum_devices(loopback=True, pa=self._pa)
        self._mic_devices = _enum_devices(loopback=False, pa=self._pa)
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
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        try:
            self._start_inner()
        except Exception as e:
            logging.exception("_start() で予期しないエラー")
            self._append_error(f"起動エラー: {e}")

    def _start_inner(self) -> None:
        if not GENAI_AVAILABLE:
            self._append_error("google-genai が未インストールです。")
            return
        api_key = self._api_key_var.get().strip()
        if not api_key:
            self._append_error("Gemini APIキーを入力してください。")
            return

        enable_listen = self._enable_listen_var.get()
        enable_speak = self._enable_speak_var.get()
        if not enable_listen and not enable_speak:
            self._append_error("「聴く」か「話す」を少なくとも1つ有効にしてください。")
            return

        loopback_idx = self._get_device_index(self._loopback_combo, self._loopback_devices) if enable_listen else None
        if enable_listen and loopback_idx is None:
            self._append_error("有効なループバックデバイスを選択してください。")
            return
        mic_idx = self._get_device_index(self._mic_combo, self._mic_devices) if enable_speak else None
        if enable_speak and mic_idx is None:
            self._append_error("有効なマイクデバイスを選択してください。")
            return

        client = genai.Client(api_key=api_key)
        context = self._context_text.get("1.0", "end").strip()
        chunk_sec = self._interval_var.get()
        ptt_enabled = self._ptt_var.get()
        use_vad = self._vad_var.get() and not ptt_enabled
        use_whisper = self._whisper_var.get() and WHISPER_AVAILABLE
        use_two_phase = self._two_phase_var.get() and not use_whisper
        self._ptt_event.clear()

        # Whisper 使用時は翻訳専用ワーカー (高速レート)
        interval = 1.0 if use_whisper else MIN_API_INTERVAL_SEC

        self._api_worker_listen = ApiWorker(self._ui_queue, client, min_interval_sec=interval, label="ApiWorker-listen")
        self._api_worker_speak  = ApiWorker(self._ui_queue, client, min_interval_sec=interval, label="ApiWorker-speak")
        self._api_worker_listen.start()
        self._api_worker_speak.start()

        if use_whisper:
            lang = None if self._whisper_lang_var.get() == "auto" else self._whisper_lang_var.get()
            self._whisper_worker = WhisperWorker(
                api_worker_listen=self._api_worker_listen,
                api_worker_speak=self._api_worker_speak,
                ui_queue=self._ui_queue,
                model_size=self._whisper_model_var.get(),
                language=lang,
                context=context,
            )
            self._whisper_worker.start()

        for stream_id, idx in [("listen", loopback_idx), ("speak", mic_idx)]:
            if idx is None:
                continue

            if use_whisper:
                def make_whisper_cb(sid: str) -> Callable[[bytes], None]:
                    return lambda wav: self._whisper_worker.submit(wav, sid)
                cb = make_whisper_cb(stream_id)
            else:
                def make_cb(sid: str, ctx: str) -> Callable[[bytes], None]:
                    return lambda wav: self._on_audio_chunk(wav, sid, ctx, use_two_phase)
                cb = make_cb(stream_id, context)

            threshold = MIC_SILENCE_RMS_THRESHOLD if stream_id == "speak" else SILENCE_RMS_THRESHOLD
            ptt_ev = self._ptt_event if (stream_id == "speak" and ptt_enabled) else None
            cap = AudioCapture(idx, chunk_sec, cb, stream_id, pa=self._pa,
                               ptt_event=ptt_ev, use_vad=use_vad, silence_threshold=threshold)
            cap.start()
            setattr(self, f"_capture_{stream_id}", cap)

        self._running = True
        self._start_btn.config(text="■ 翻訳停止")
        streams = [s for s, en in [("聴く", enable_listen), ("話す", enable_speak)] if en]
        mode = "Whisper" if use_whisper else ("2フェーズ" if use_two_phase else "通常")
        self._status_var.set(f"状態: 翻訳中... ({'+'.join(streams)}, {mode})")

        if enable_speak and ptt_enabled:
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
        self._ptt_event.clear()
        for event in _PTT_BINDINGS:
            self.root.unbind(event)
        self._ptt_btn.config(state="disabled", text="🎙 録音 (押して話す)", bg="#FF8C00")
        for attr in ("_capture_listen", "_capture_speak"):
            cap = getattr(self, attr)
            if cap:
                cap.stop()
            setattr(self, attr, None)
        if self._whisper_worker:
            self._whisper_worker.stop()
            self._whisper_worker = None
        for w in (self._api_worker_listen, self._api_worker_speak):
            if w:
                w.stop()
        self._api_worker_listen = None
        self._api_worker_speak = None
        while not self._ui_queue.empty():
            try:
                self._ui_queue.get_nowait()
            except queue.Empty:
                break
        self._stream_buffers.clear()
        self._running = False
        self._start_btn.config(text="▶ 翻訳開始")
        self._status_var.set("状態: 停止中")

    def _on_audio_chunk(self, wav_bytes: bytes, stream_id: str, context: str, two_phase: bool) -> None:
        worker = self._api_worker_listen if stream_id == "listen" else self._api_worker_speak
        if worker is None:
            return
        if two_phase:
            worker.submit(ApiRequest(
                wav_bytes=wav_bytes,
                prompt=build_stt_prompt(stream_id),
                stream_id=stream_id, phase=1, context=context,
            ))
        else:
            worker.submit(ApiRequest(
                wav_bytes=wav_bytes,
                prompt=build_prompt(stream_id, context),
                stream_id=stream_id, phase=0,
            ))

    def _ptt_press(self) -> None:
        if self._running and self._capture_speak:
            self._ptt_event.set()
            self._ptt_btn.config(text="🔴 録音中...", bg="#CC0000")
            self._status_var.set("状態: 🎙 録音中 (Space/ボタンを離すと送信)")

    def _ptt_release(self) -> None:
        self._ptt_event.clear()
        if self._ptt_btn["state"] != "disabled":
            self._ptt_btn.config(text="🎙 録音 (押して話す)", bg="#FF8C00")
            streams = [s for s, cap in [("聴く", self._capture_listen), ("話す", self._capture_speak)] if cap]
            self._status_var.set(f"状態: 翻訳中... ({'+'.join(streams)})")

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
                elif kind == "result":
                    # legacy (未使用だが互換性のため残す)
                    _, stream_id, ts, text = item
                    if "(無音)" not in text:
                        self._append_result(stream_id, ts, text)
                elif kind == "error":
                    _, stream_id, msg = item
                    self._append_error(f"[{stream_id}] {msg}")
                elif kind == "status":
                    _, msg = item
                    self._status_var.set(f"状態: {msg}")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _on_partial_start(self, stream_id: str, ts: str) -> None:
        label, tag = _STREAM_META[stream_id]
        self._result_text.config(state="normal")
        mark = self._result_text.index("end")  # frozen index (rollback用)
        self._result_text.insert("end", f"[{ts}] {label}\n", tag)
        self._result_text.see("end")
        self._result_text.config(state="disabled")
        self._stream_buffers[stream_id] = {"text": "", "mark": mark}

    def _on_partial(self, stream_id: str, text: str) -> None:
        if stream_id not in self._stream_buffers:
            return
        self._stream_buffers[stream_id]["text"] += text
        self._result_text.config(state="normal")
        self._result_text.insert("end", text)
        self._result_text.see("end")
        self._result_text.config(state="disabled")

    def _on_partial_end(self, stream_id: str) -> None:
        buf = self._stream_buffers.pop(stream_id, None)
        if buf is None:
            return
        accumulated = buf["text"]
        if "(無音)" in accumulated:
            # ロールバック: ヘッダと本文を削除
            self._result_text.config(state="normal")
            self._result_text.delete(buf["mark"], "end")
            self._result_text.config(state="disabled")
        else:
            self._result_text.config(state="normal")
            self._result_text.insert("end", "\n" + "─" * 50 + "\n", "separator")
            self._result_text.see("end")
            self._result_text.config(state="disabled")

    def _on_transcript(self, stream_id: str, ts: str, text: str) -> None:
        label, tag = _STREAM_META[stream_id]
        with self._editable_result():
            self._result_text.insert("end", f"[{ts}] {label}\n", tag)
            self._result_text.insert("end", f"原文: {text}\n", "original")

    def _append_result(self, stream_id: str, ts: str, text: str) -> None:
        label, tag = _STREAM_META[stream_id]
        with self._editable_result():
            self._result_text.insert("end", f"[{ts}] {label}\n", tag)
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("原文:"):
                    self._result_text.insert("end", stripped + "\n", "original")
                elif stripped.startswith("訳文:"):
                    self._result_text.insert("end", stripped + "\n", "translation")
                elif stripped:
                    self._result_text.insert("end", stripped + "\n")
            self._result_text.insert("end", "─" * 50 + "\n", "separator")

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
            }
            CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            self._status_var.set("状態: 設定を保存しました")
        except Exception as e:
            self._append_error(f"設定保存失敗: {e}")

    def _load_config(self) -> None:
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self._api_key_var.set(config.get("api_key", ""))
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
        except Exception:
            pass

    # ─────────────────────────── 終了処理 ───────────────────────────

    def on_close(self) -> None:
        self.root.withdraw()
        self._save_config()
        self._stop()
        if self._pa:
            self._pa.terminate()
            self._pa = None
        self.root.destroy()


def main() -> None:
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    root = tk.Tk()
    root.minsize(700, 620)
    app = TranslatorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)

    def _tk_exception_handler(exc, val, tb):
        logging.error("tkinter callback exception", exc_info=(exc, val, tb))
        app._append_error(f"内部エラー: {val}")
    root.report_callback_exception = _tk_exception_handler

    root.mainloop()


if __name__ == "__main__":
    main()
