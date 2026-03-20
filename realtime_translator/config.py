"""設定の保存・読み込み"""
import json
import logging
import os
import stat

from .constants import CONFIG_PATH
from .stream_modes import normalize_translation_mode

_KEYRING_SERVICE = "realtime-translator"
_KEYRING_USERNAME = "gemini-api-key"

# Multi-provider keyring service names
_PROVIDER_KEYRING: dict[str, tuple[str, str]] = {
    "gemini":     ("realtime-translator-gemini",     "api-key"),
    "openai":     ("realtime-translator-openai",     "api-key"),
    "openrouter": ("realtime-translator-openrouter", "api-key"),
}

_VALID_INTERVALS = (1, 2, 3, 5, 8)
_DEFAULT_INTERVAL = 5

try:
    import keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    _KEYRING_AVAILABLE = False


_keyring_usable_cache: bool | None = None


def _keyring_usable() -> bool:
    """keyring が import 可能かつ backend が実際に動作するか確認（結果はキャッシュ）"""
    global _keyring_usable_cache
    if _keyring_usable_cache is not None:
        return _keyring_usable_cache
    if not _KEYRING_AVAILABLE:
        _keyring_usable_cache = False
        return False
    try:
        keyring.get_password(_KEYRING_SERVICE, "__probe__")
        _keyring_usable_cache = True
        return True
    except Exception:
        logging.warning("keyring backend が利用不可。JSONフォールバックを使用します")
        _keyring_usable_cache = False
        return False


def save_api_key(api_key: str, provider: str = "gemini") -> bool:
    """APIキーをkeyringに保存する。成功時True、失敗時False"""
    if not _keyring_usable():
        return False
    service, username = _PROVIDER_KEYRING.get(provider, (_KEYRING_SERVICE, _KEYRING_USERNAME))
    try:
        keyring.set_password(service, username, api_key)
        return True
    except Exception:
        logging.exception("keyring へのAPIキー保存に失敗 (provider=%s)", provider)
        return False


def load_api_key(provider: str = "gemini") -> str:
    """keyringからAPIキーを取得する"""
    if not _keyring_usable():
        return ""
    service, username = _PROVIDER_KEYRING.get(provider, (_KEYRING_SERVICE, _KEYRING_USERNAME))
    try:
        result = keyring.get_password(service, username) or ""
        # Auto-migrate: old single-key → new provider-specific key
        if not result and provider == "gemini":
            old = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME) or ""
            if old:
                save_api_key(old, "gemini")
                logging.info("旧keyringエントリをgeminiプロバイダーに移行しました")
                return old
        return result
    except Exception:
        logging.exception("keyring からのAPIキー取得に失敗 (provider=%s)", provider)
        return ""


def _sanitize_interval(value) -> int:
    """interval 値を検証し、不正なら既定値を返す"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL
    return v if v in _VALID_INTERVALS else _DEFAULT_INTERVAL


def _sanitize_api_interval(value) -> float:
    """api_interval 値を検証し、不正なら 0.0（自動）を返す"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if v >= 0.0 else 0.0


def _restrict_file_permissions(path) -> None:
    """ファイルのアクセス権限を所有者のみに制限する（ベストエフォート）"""
    try:
        if os.name == "nt":
            # Windows: 読み書き権限のみ（他ユーザーのアクセスは完全には制限できない）
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        else:
            os.chmod(path, 0o600)
    except OSError:
        logging.warning("設定ファイルの権限設定に失敗: %s", path)


_API_KEY_FIELDS = {
    "api_key": "gemini",
    "openai_api_key": "openai",
    "openrouter_api_key": "openrouter",
}


def save_config(data: dict) -> None:
    """設定をJSONファイルに保存する。APIキーはkeyringに分離"""
    data = dict(data)  # 呼び出し元の辞書を変更しないようにコピー

    if "interval" in data:
        data["interval"] = _sanitize_interval(data["interval"])
    if "api_interval" in data:
        data["api_interval"] = _sanitize_api_interval(data["api_interval"])
    if "pc_audio_mode" in data:
        data["pc_audio_mode"] = normalize_translation_mode(data["pc_audio_mode"], "en_ja")
    if "mic_mode" in data:
        data["mic_mode"] = normalize_translation_mode(data["mic_mode"], "ja_en")

    # Extract all API keys and save to keyring
    for field, provider in _API_KEY_FIELDS.items():
        key = data.pop(field, "")
        if key:
            saved = save_api_key(key, provider)
            if not saved:
                data[field] = key  # fallback to JSON

    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _restrict_file_permissions(CONFIG_PATH)
    except Exception:
        logging.exception("設定保存に失敗")
        raise


def load_config() -> dict:
    """設定をJSONファイルから読み込む。APIキーはkeyringから取得"""
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except json.JSONDecodeError:
        logging.warning("設定ファイルが壊れています: %s", CONFIG_PATH)
        config = {}
    except Exception:
        logging.exception("設定読み込みに失敗")
        config = {}

    if "interval" in config:
        config["interval"] = _sanitize_interval(config["interval"])
    config["api_interval"] = _sanitize_api_interval(config.get("api_interval", 0.0))
    config["pc_audio_mode"] = normalize_translation_mode(config.get("pc_audio_mode"), "en_ja")
    config["mic_mode"] = normalize_translation_mode(config.get("mic_mode"), "ja_en")

    # Migrate old JSON api_key to keyring
    json_key = config.pop("api_key", "")
    if json_key and _keyring_usable():
        if save_api_key(json_key, "gemini"):
            logging.info("APIキーをkeyringに移行しました")
            try:
                CONFIG_PATH.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                logging.exception("移行後の設定保存に失敗")

    # Load all provider keys from keyring (with JSON fallback)
    for field, provider in _API_KEY_FIELDS.items():
        json_fallback = config.pop(field, "")
        kr_key = load_api_key(provider)
        config[field] = kr_key or json_fallback

    # Backward compat: ensure api_key has gemini key or old json_key fallback
    if not config.get("api_key") and json_key:
        config["api_key"] = json_key

    return config
