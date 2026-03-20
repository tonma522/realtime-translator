"""Tests for deterministic translation annotation."""

from realtime_translator.translation_postprocess import annotate_translation


def test_length_annotation_adds_inches_and_english_reading():
    assert (
        annotate_translation("12 mm", output_language="en")
        == "12 mm (0.47 in, twelve millimeters)"
    )


def test_pressure_annotation_from_psi_adds_mpa_and_bar():
    assert (
        annotate_translation("35 psi", output_language="ja")
        == "35 psi (0.24 MPa / 2.41 bar)"
    )


def test_identifier_is_not_read_as_spoken_number():
    assert annotate_translation("Use M8 bolt", output_language="en") == "Use M8 bolt"


def test_tolerance_keeps_precision():
    assert "0.0004 in" in annotate_translation("±0.01 mm", output_language="en")


def test_mesh_lookup_uses_fixed_table_value():
    annotated = annotate_translation("100 mesh", output_language="en")
    assert "149 micron" in annotated
    assert "one hundred mesh" in annotated


def test_partial_failure_returns_original_text(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("realtime_translator.translation_postprocess._annotate_text", _boom)
    assert annotate_translation("12 mm", output_language="en") == "12 mm"
