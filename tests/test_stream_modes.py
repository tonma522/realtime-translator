import pytest

from realtime_translator.stream_modes import (
    normalize_translation_mode,
    resolve_virtual_stream_id,
)


def test_normalize_translation_mode_invalid_value_falls_back_to_default():
    assert normalize_translation_mode("bad", "en_ja") == "en_ja"


def test_resolve_virtual_stream_id_for_pc_audio_auto():
    assert resolve_virtual_stream_id("listen", "auto") == "listen_auto"


def test_resolve_virtual_stream_id_rejects_unknown_stream():
    with pytest.raises(ValueError, match="unknown stream_id"):
        resolve_virtual_stream_id("other", "auto")
