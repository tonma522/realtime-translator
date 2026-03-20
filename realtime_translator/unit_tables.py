"""Fixed conversion constants and abrasive lookup tables for translation annotation."""

from decimal import Decimal

# Engineering conversion constants.
MM_PER_INCH = Decimal("25.4")
LB_PER_KG = Decimal("2.20462")
LBF_FT_PER_NM = Decimal("0.737562")
PSI_PER_MPA = Decimal("145.037738")
PSI_PER_BAR = Decimal("14.5037738")

# Representative abrasive tables.
# JIS/FEPA rows are practical near-equivalents and should be treated as fixed lookup values.
ABRASIVE_TABLE = {
    "#400": {"micron": 35, "fepa": "P400"},
    "P400": {"micron": 35, "jis": "#400"},
}

MESH_TABLE = {
    "100": {"micron": 149},
}

MICRON_TABLE = {
    35: {"jis": "#400", "fepa": "P400"},
    149: {"mesh": "100 mesh"},
}
