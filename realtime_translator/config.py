"""設定の保存・読み込み"""
import json
import logging

from .constants import CONFIG_PATH


def save_config(data: dict) -> None:
    """設定をJSONファイルに保存する（api_keyは含めない）"""
    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        logging.exception("設定保存に失敗")
        raise


def load_config() -> dict:
    """設定をJSONファイルから読み込む"""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.warning("設定ファイルが壊れています: %s", CONFIG_PATH)
        return {}
    except Exception:
        logging.exception("設定読み込みに失敗")
        return {}
