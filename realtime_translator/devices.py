"""デバイス列挙"""
from .constants import PYAUDIO_AVAILABLE, pyaudio


def enum_devices(loopback: bool, pa=None) -> list[dict]:
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
