"""デバイス列挙テスト"""
from unittest.mock import MagicMock, patch

import pytest

from realtime_translator import devices as devices_module
from realtime_translator.devices import enum_devices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pa_mock(device_infos: list[dict]):
    """Create a mock PyAudio with given device info list."""
    pa = MagicMock()
    pa.get_device_count.return_value = len(device_infos)
    pa.get_device_info_by_index.side_effect = lambda i: device_infos[i]
    return pa


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnumDevicesLoopback:
    def test_returns_loopback_devices(self):
        infos = [
            {"name": "Speaker (loopback)", "isLoopbackDevice": True, "maxInputChannels": 0},
            {"name": "Microphone", "isLoopbackDevice": False, "maxInputChannels": 2},
            {"name": "HDMI (loopback)", "isLoopbackDevice": True, "maxInputChannels": 0},
        ]
        pa = _make_pa_mock(infos)

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=True, pa=pa)

        assert len(result) == 2
        assert result[0] == {"index": 0, "name": "Speaker (loopback)"}
        assert result[1] == {"index": 2, "name": "HDMI (loopback)"}

    def test_excludes_non_loopback(self):
        infos = [
            {"name": "Microphone", "isLoopbackDevice": False, "maxInputChannels": 2},
        ]
        pa = _make_pa_mock(infos)

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=True, pa=pa)

        assert result == []


class TestEnumDevicesMicrophone:
    def test_returns_input_devices(self):
        infos = [
            {"name": "Microphone", "isLoopbackDevice": False, "maxInputChannels": 2},
            {"name": "Speaker", "isLoopbackDevice": False, "maxInputChannels": 0},
            {"name": "USB Mic", "isLoopbackDevice": False, "maxInputChannels": 1},
        ]
        pa = _make_pa_mock(infos)

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=False, pa=pa)

        assert len(result) == 2
        assert result[0] == {"index": 0, "name": "Microphone"}
        assert result[1] == {"index": 2, "name": "USB Mic"}

    def test_excludes_loopback_from_microphone_list(self):
        """Loopback devices with input channels should NOT appear in mic list."""
        infos = [
            {"name": "Loopback Mic", "isLoopbackDevice": True, "maxInputChannels": 2},
            {"name": "Real Mic", "isLoopbackDevice": False, "maxInputChannels": 1},
        ]
        pa = _make_pa_mock(infos)

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=False, pa=pa)

        assert len(result) == 1
        assert result[0]["name"] == "Real Mic"

    def test_excludes_zero_input_channels(self):
        infos = [
            {"name": "Output Only", "isLoopbackDevice": False, "maxInputChannels": 0},
        ]
        pa = _make_pa_mock(infos)

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=False, pa=pa)

        assert result == []


class TestEnumDevicesEdgeCases:
    def test_empty_device_list(self):
        pa = _make_pa_mock([])

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=True, pa=pa)

        assert result == []

    def test_empty_device_list_microphone(self):
        pa = _make_pa_mock([])

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            result = enum_devices(loopback=False, pa=pa)

        assert result == []

    def test_pyaudio_unavailable_returns_empty(self):
        with patch.object(devices_module, "PYAUDIO_AVAILABLE", False):
            result = enum_devices(loopback=True)

        assert result == []

    def test_pyaudio_unavailable_mic_returns_empty(self):
        with patch.object(devices_module, "PYAUDIO_AVAILABLE", False):
            result = enum_devices(loopback=False)

        assert result == []

    def test_missing_isLoopbackDevice_defaults_false(self):
        """Device info without isLoopbackDevice field defaults to False."""
        infos = [
            {"name": "Mic", "maxInputChannels": 1},
        ]
        pa = _make_pa_mock(infos)

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            # Should appear in mic list (not loopback)
            result_mic = enum_devices(loopback=False, pa=pa)
            result_lb = enum_devices(loopback=True, pa=pa)

        assert len(result_mic) == 1
        assert result_lb == []


class TestEnumDevicesOwnership:
    def test_provided_pa_is_not_terminated(self):
        pa = _make_pa_mock([])

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True):
            enum_devices(loopback=True, pa=pa)

        pa.terminate.assert_not_called()

    def test_own_pa_is_terminated(self):
        mock_pa_instance = _make_pa_mock([])

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True), \
             patch.object(devices_module, "pyaudio") as mock_pyaudio_mod:
            mock_pyaudio_mod.PyAudio.return_value = mock_pa_instance
            enum_devices(loopback=True, pa=None)

        mock_pa_instance.terminate.assert_called_once()

    def test_own_pa_terminated_even_on_error(self):
        """If enumeration raises, own PyAudio instance is still terminated."""
        mock_pa_instance = MagicMock()
        mock_pa_instance.get_device_count.side_effect = OSError("PortAudio error")

        with patch.object(devices_module, "PYAUDIO_AVAILABLE", True), \
             patch.object(devices_module, "pyaudio") as mock_pyaudio_mod:
            mock_pyaudio_mod.PyAudio.return_value = mock_pa_instance
            with pytest.raises(OSError):
                enum_devices(loopback=True, pa=None)

        mock_pa_instance.terminate.assert_called_once()
