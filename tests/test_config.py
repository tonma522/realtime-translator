"""設定の保存・読み込みテスト"""
import json
from unittest.mock import patch

import pytest

from realtime_translator import config as config_module
from realtime_translator.config import (
    save_config, load_config, save_api_key, load_api_key,
    _sanitize_interval, _VALID_INTERVALS, _DEFAULT_INTERVAL,
)


@pytest.fixture(autouse=True)
def _reset_keyring_cache():
    """全テストの前後でkeyringキャッシュをリセット（順序依存防止）"""
    config_module._keyring_usable_cache = None
    yield
    config_module._keyring_usable_cache = None


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
        if config_module._KEYRING_AVAILABLE:
            assert "api_key" not in raw

    def test_keyring_save_load(self):
        if not config_module._KEYRING_AVAILABLE:
            pytest.skip("keyring not available")
        save_api_key("test-roundtrip-key")
        assert load_api_key() == "test-roundtrip-key"

    def test_keyring_failure_falls_back_to_json(self, tmp_config):
        """keyring backend が失敗した場合、APIキーがJSONに保存される"""
        with patch.object(config_module, "_keyring_usable", return_value=False):
            save_config({"api_key": "fallback-key", "interval": 5})
            raw = json.loads(tmp_config.read_text(encoding="utf-8"))
            assert raw["api_key"] == "fallback-key"

    def test_keyring_failure_load_falls_back_to_json(self, tmp_config):
        """keyring 不可時、JSONのapi_keyが返される"""
        tmp_config.write_text(
            json.dumps({"api_key": "json-key", "interval": 5}), encoding="utf-8"
        )
        with patch.object(config_module, "_keyring_usable", return_value=False):
            result = load_config()
            assert result["api_key"] == "json-key"


class TestSanitizeInterval:
    @pytest.mark.parametrize("value,expected", [
        (3, 3), (5, 5), (8, 8),
        (0, _DEFAULT_INTERVAL),
        (10, _DEFAULT_INTERVAL),
        (-1, _DEFAULT_INTERVAL),
        ("5", 5),
        ("invalid", _DEFAULT_INTERVAL),
        (None, _DEFAULT_INTERVAL),
        (5.5, 5),
    ])
    def test_sanitize_interval(self, value, expected):
        assert _sanitize_interval(value) == expected

    def test_save_sanitizes_interval(self, tmp_config):
        save_config({"interval": 99})
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert raw["interval"] == _DEFAULT_INTERVAL

    def test_load_sanitizes_interval(self, tmp_config):
        tmp_config.write_text(json.dumps({"interval": 42}), encoding="utf-8")
        result = load_config()
        assert result["interval"] == _DEFAULT_INTERVAL


class TestKeyringUsableCache:
    def test_probe_called_once_on_repeated_calls(self):
        """_keyring_usable() を2回呼んでも __probe__ プローブは1回だけ"""
        from realtime_translator.config import _keyring_usable
        call_count = 0
        original_get = config_module.keyring.get_password if config_module._KEYRING_AVAILABLE else None

        def counting_get(service, username):
            nonlocal call_count
            if username == "__probe__":
                call_count += 1
            return original_get(service, username) if original_get else None

        if not config_module._KEYRING_AVAILABLE:
            pytest.skip("keyring not available")

        with patch.object(config_module.keyring, "get_password", side_effect=counting_get):
            _keyring_usable()
            _keyring_usable()
        assert call_count == 1

    def test_cache_retains_false_on_failure(self):
        """プローブ失敗時、キャッシュは False を維持する"""
        from realtime_translator.config import _keyring_usable

        with patch.object(config_module, "_KEYRING_AVAILABLE", True), \
             patch.object(config_module, "keyring") as mock_kr:
            mock_kr.get_password.side_effect = Exception("backend error")
            assert _keyring_usable() is False
            assert config_module._keyring_usable_cache is False
            # 2回目はキャッシュから返す（get_password は呼ばれない）
            mock_kr.get_password.reset_mock()
            assert _keyring_usable() is False
            mock_kr.get_password.assert_not_called()

    def test_not_available_returns_false(self):
        """_KEYRING_AVAILABLE が False なら即 False"""
        from realtime_translator.config import _keyring_usable
        with patch.object(config_module, "_KEYRING_AVAILABLE", False):
            assert _keyring_usable() is False

    def test_cache_reset_allows_reprobe(self):
        """キャッシュを None にリセットすると再プローブが走る"""
        from realtime_translator.config import _keyring_usable

        with patch.object(config_module, "_KEYRING_AVAILABLE", True), \
             patch.object(config_module, "keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            _keyring_usable()
            assert mock_kr.get_password.call_count == 1

            config_module._keyring_usable_cache = None
            _keyring_usable()
            assert mock_kr.get_password.call_count == 2


class TestKeyringMigration:
    """load_config の JSON→keyring 移行パスのテスト"""

    def test_successful_migration(self, tmp_config):
        """JSON に api_key がありkeyringが使える場合、keyringに移行しJSONから削除"""
        tmp_config.write_text(
            json.dumps({"api_key": "migrate-me", "interval": 5}), encoding="utf-8"
        )
        with patch.object(config_module, "_keyring_usable", return_value=True), \
             patch.object(config_module, "save_api_key", return_value=True) as mock_save, \
             patch.object(config_module, "load_api_key", return_value="migrate-me"):
            result = load_config()

        # save_api_key が正しいキーで呼ばれた
        mock_save.assert_called_once_with("migrate-me")
        # 返り値に正しいapi_keyが含まれる
        assert result["api_key"] == "migrate-me"
        # JSONファイルからapi_keyが削除されている
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert "api_key" not in raw
        # 他のフィールドは保持されている
        assert raw["interval"] == 5

    def test_failed_migration_key_stays_in_json(self, tmp_config):
        """keyring保存が失敗した場合、api_keyはJSONに残り返り値にも含まれる"""
        tmp_config.write_text(
            json.dumps({"api_key": "keep-me", "interval": 3}), encoding="utf-8"
        )
        with patch.object(config_module, "_keyring_usable", return_value=True), \
             patch.object(config_module, "save_api_key", return_value=False), \
             patch.object(config_module, "load_api_key", return_value=""):
            result = load_config()

        # keyring保存失敗 → json_key がフォールバックとして返される
        assert result["api_key"] == "keep-me"
        # JSONファイルは書き換えられていない（移行が発生しないので元のまま）
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        # config.pop で api_key は取り出されるが、save_api_key失敗時は
        # JSONの再書き込みが行われないので元ファイルが残る
        assert raw["api_key"] == "keep-me"

    def test_no_migration_needed_no_api_key(self, tmp_config):
        """JSON に api_key がなければ移行は発生しない"""
        tmp_config.write_text(
            json.dumps({"interval": 8}), encoding="utf-8"
        )
        with patch.object(config_module, "_keyring_usable", return_value=True), \
             patch.object(config_module, "save_api_key") as mock_save, \
             patch.object(config_module, "load_api_key", return_value="kr-key"):
            result = load_config()

        # save_api_key は呼ばれない
        mock_save.assert_not_called()
        # keyringからのキーが返される
        assert result["api_key"] == "kr-key"

    def test_no_migration_when_keyring_unusable(self, tmp_config):
        """keyringが使えない場合、api_keyはJSONから直接返される"""
        tmp_config.write_text(
            json.dumps({"api_key": "json-only", "interval": 5}), encoding="utf-8"
        )
        with patch.object(config_module, "_keyring_usable", return_value=False), \
             patch.object(config_module, "save_api_key") as mock_save, \
             patch.object(config_module, "load_api_key", return_value=""):
            result = load_config()

        mock_save.assert_not_called()
        assert result["api_key"] == "json-only"
