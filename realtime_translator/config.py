"""設定の保存・読み込み"""
import json
import logging

from .constants import CONFIG_PATH

_KEYRING_SERVICE = "realtime-translator"
_KEYRING_USERNAME = "gemini-api-key"

try:
    import keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    _KEYRING_AVAILABLE = False


def save_api_key(api_key: str) -> None:
    """APIキーをkeyringに保存する。非対応時はログ警告のみ"""
    if not _KEYRING_AVAILABLE:
        logging.warning("keyring が未インストール。APIキーは設定ファイルに平文保存されます")
        return
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
    except Exception:
        logging.exception("keyring へのAPIキー保存に失敗")


def load_api_key() -> str:
    """keyringからAPIキーを取得する"""
    if not _KEYRING_AVAILABLE:
        return ""
    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME) or ""
    except Exception:
        logging.exception("keyring からのAPIキー取得に失敗")
        return ""


def save_config(data: dict) -> None:
    """設定をJSONファイルに保存する。APIキーはkeyringに分離"""
    api_key = data.pop("api_key", "")
    if api_key:
        save_api_key(api_key)
        if not _KEYRING_AVAILABLE:
            data["api_key"] = api_key
    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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

    # 旧JSONにapi_keyがあればkeyringに移行
    json_key = config.pop("api_key", "")
    if json_key and _KEYRING_AVAILABLE:
        save_api_key(json_key)
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
