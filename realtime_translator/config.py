"""設定の保存・読み込み"""
import json
import logging
import os
import stat

from .constants import CONFIG_PATH

_KEYRING_SERVICE = "realtime-translator"
_KEYRING_USERNAME = "gemini-api-key"

_VALID_INTERVALS = (3, 5, 8)
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


def save_api_key(api_key: str) -> bool:
    """APIキーをkeyringに保存する。成功時True、失敗時False"""
    if not _keyring_usable():
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
        return True
    except Exception:
        logging.exception("keyring へのAPIキー保存に失敗")
        return False


def load_api_key() -> str:
    """keyringからAPIキーを取得する"""
    if not _keyring_usable():
        return ""
    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME) or ""
    except Exception:
        logging.exception("keyring からのAPIキー取得に失敗")
        return ""


def _sanitize_interval(value) -> int:
    """interval 値を検証し、不正なら既定値を返す"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL
    return v if v in _VALID_INTERVALS else _DEFAULT_INTERVAL


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


def save_config(data: dict) -> None:
    """設定をJSONファイルに保存する。APIキーはkeyringに分離"""
    data = dict(data)  # 呼び出し元の辞書を変更しないようにコピー
    api_key = data.pop("api_key", "")
    if "interval" in data:
        data["interval"] = _sanitize_interval(data["interval"])
    if api_key:
        saved_to_keyring = save_api_key(api_key)
        if not saved_to_keyring:
            data["api_key"] = api_key
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

    # 旧JSONにapi_keyがあればkeyringに移行
    json_key = config.pop("api_key", "")
    if json_key and _keyring_usable():
        if save_api_key(json_key):
            logging.info("APIキーをkeyringに移行しました")
            try:
                CONFIG_PATH.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                logging.exception("移行後の設定保存に失敗")

    # keyringからAPIキーを取得（なければJSON由来のキーをフォールバック）
    kr_key = load_api_key()
    config["api_key"] = kr_key or json_key

    return config
