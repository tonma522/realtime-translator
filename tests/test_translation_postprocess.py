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


def test_pressure_annotation_from_mpa_uses_single_target():
    assert (
        annotate_translation("0.24 MPa", output_language="ja")
        == "0.24 MPa (34.81 psi)"
    )


def test_identifier_is_not_read_as_spoken_number():
    assert annotate_translation("Use M8 bolt", output_language="en") == "Use M8 bolt"


def test_tolerance_keeps_precision():
    assert "0.0004 in" in annotate_translation("±0.01 mm", output_language="en")


def test_mesh_lookup_uses_fixed_table_value():
    annotated = annotate_translation("100 mesh", output_language="en")
    assert "149 micron" in annotated
    assert "one hundred mesh" in annotated


def test_temperature_annotation_keeps_one_decimal_place():
    assert annotate_translation("-10 C", output_language="ja") == "-10 C (14.0 F)"


def test_grit_without_standard_marker_remains_unchanged():
    assert annotate_translation("Use 35 grit belt", output_language="en") == "Use 35 grit belt"


def test_fullwidth_digits_are_normalized_and_annotated():
    assert (
        annotate_translation("１２ mm", output_language="en")
        == "12 mm (0.47 in, twelve millimeters)"
    )


def test_decimal_comma_ra_is_normalized():
    assert (
        annotate_translation("Ra ０,８", output_language="en")
        == "Ra 0.8 (Ra 0.8 um, R-A zero point eight)"
    )


def test_partial_failure_returns_original_text(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("realtime_translator.translation_postprocess._annotate_text", _boom)
    assert annotate_translation("12 mm", output_language="en") == "12 mm"
