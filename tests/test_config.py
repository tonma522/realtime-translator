"""設定の保存・読み込みテスト"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from realtime_translator import config as config_module
from realtime_translator.config import save_config, load_config, save_api_key, load_api_key


@pytest.fixture
def tmp_config(tmp_path):
    """一時的なCONFIG_PATHを使用"""
    fake_path = tmp_path / "test_config.json"
    with patch.object(config_module, "CONFIG_PATH", fake_path):
        yield fake_path


class TestSaveLoadConfig:
    def test_roundtrip(self, tmp_config):
        data = {"interval": 5, "enable_listen": True, "context": "テスト"}
        save_config(data.copy())
        loaded = load_config()
        assert loaded["interval"] == 5
        assert loaded["enable_listen"] is True
        assert loaded["context"] == "テスト"

    def test_missing_file_returns_empty_or_keyring_only(self, tmp_config):
        result = load_config()
        # ファイルがない場合、keyringのキーだけが入る可能性がある
        non_key_fields = {k: v for k, v in result.items() if k != "api_key"}
        assert non_key_fields == {}

    def test_corrupt_json_returns_empty_or_keyring_only(self, tmp_config):
        tmp_config.write_text("{broken json", encoding="utf-8")
        result = load_config()
        non_key_fields = {k: v for k, v in result.items() if k != "api_key"}
        assert non_key_fields == {}


class TestApiKeyStorage:
    def test_api_key_not_in_json(self, tmp_config):
        save_config({"api_key": "secret-key", "interval": 3})
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        # keyringが使える場合はJSONにapi_keyが含まれない
        if config_module._KEYRING_AVAILABLE:
            assert "api_key" not in raw

    def test_keyring_save_load(self):
        if not config_module._KEYRING_AVAILABLE:
            pytest.skip("keyring not available")
        save_api_key("test-roundtrip-key")
        assert load_api_key() == "test-roundtrip-key"
