"""v3.8.8 regression: RAM identity gate for lookup matches.

"Innodisk 8GB DDR4 2400 ECC DIMM" auto-accepted an A-Tech Non-ECC stick:
score_candidate gave 92, validate_result passed it (and even passed 16GB),
and the DB auto-accept path never called validate_result at all.
"""
import pytest

from app.scrapers.validation import (
    extract_ram_ecc,
    extract_ram_generation,
    extract_ram_speed,
    extract_ram_vendor,
    ram_candidate_decision,
    ram_spec_conflicts,
    validate_result,
)

ATECH = ("A-Tech 8GB DDR4-2400 DIMM PC4-19200 UDIMM Non-ECC 2Rx8 1.2V CL17 "
         "288-Pin Desktop Computer RAM Module")


@pytest.mark.parametrize("text,expected", [
    ("DDR4-2400", 2400),
    ("8GB DDR4 2400 ECC DIMM", 2400),
    ("PC4-19200 UDIMM", 2400),
    ("PC3-14900R", 1866),
    ("PC3-12800R-11-12-E2", 1600),
    ("PC3-10600E", 1333),
    ("PC4-2400T-R", 2400),
    ("PC4-2666V-RB2", 2666),
    ("DDR5 6000MHz", 6000),
    ("DDR4 288-Pin", None),
    ("8GB stick", None),
])
def test_extract_ram_speed(text, expected):
    assert extract_ram_speed(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("UDIMM Non-ECC", False),
    ("non ecc unbuffered", False),
    ("NonECC", False),
    ("8GB DDR4 2400 ECC DIMM", True),
    ("16GB 2Rx4 PC3-14900R RDIMM", True),
    ("32GB LRDIMM", True),
    ("Registered", True),
    ("8GB DDR4 UDIMM", None),
])
def test_extract_ram_ecc(text, expected):
    assert extract_ram_ecc(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("DDR4-2400", 4), ("PC3-14900R", 3), ("PC3L-12800R", 3),
    ("DDR3L-1600", 3), ("DDR5 6000", 5), ("8GB RAM", None),
])
def test_extract_ram_generation(text, expected):
    assert extract_ram_generation(text) == expected


def test_extract_ram_vendor():
    assert extract_ram_vendor(ATECH) == "atech"
    assert extract_ram_vendor("Innodisk 8GB DDR4") == "innodisk"
    assert extract_ram_vendor("G.Skill Trident Z") == "gskill"
    assert extract_ram_vendor("8GB DDR4 2400") is None


def test_the_reported_pair_is_rejected():
    query = "Innodisk 8GB DDR4 2400 ECC DIMM"
    assert ram_spec_conflicts(query, ATECH) == ["ecc"]
    assert validate_result(query, ATECH, "RAM") is False
    assert ram_candidate_decision(query, ATECH) == "reject"


def test_query_without_vendor_still_rejected_on_ecc():
    assert ram_candidate_decision("8GB DDR4 2400 ECC DIMM", ATECH, "Innodisk", "A-Tech") == "reject"


def test_wrong_capacity_rejected():
    assert "capacity" in ram_spec_conflicts("16GB DDR4 2400 Non-ECC", ATECH)
    assert validate_result("16GB DDR4 2400 Non-ECC", ATECH, "RAM") is False


def test_wrong_speed_and_generation_rejected():
    assert ram_spec_conflicts("8GB DDR4-3200 Non-ECC", ATECH) == ["speed"]
    assert "generation" in ram_spec_conflicts("8GB DDR3-1600", ATECH)


def test_pc_rating_matches_ddr_speed():
    assert ram_spec_conflicts("A-Tech 8GB PC4-19200 Non-ECC", ATECH) == []
    assert validate_result("A-Tech 8GB DDR4-2400 Non-ECC", ATECH, "RAM") is True


def test_vendor_conflict_goes_to_review_not_reject():
    # Same specs, different brand: could be an OEM rebrand.
    assert ram_candidate_decision("Kingston 8GB DDR4-2400 Non-ECC", ATECH) == "review"
    assert ram_candidate_decision("8GB DDR4-2400 Non-ECC", ATECH, "Kingston", "A-Tech") == "review"
    assert validate_result("Kingston 8GB DDR4-2400 Non-ECC", ATECH, "RAM") is False


def test_unstated_fields_never_conflict():
    assert ram_candidate_decision("8GB DDR4", ATECH) == "ok"
    assert ram_candidate_decision("A-Tech DDR4 UDIMM", ATECH, None, "A-Tech") == "ok"


# ── v3.8.9: a plain size may be the kit total or one stick ─────────────────

@pytest.mark.parametrize("query,candidate,conflict", [
    ("32GB DDR4-3200", "Team T-FORCE VULCAN Z 32GB (2 x 16GB) DDR4 3200", False),  # kit total
    ("16GB DDR4-3200", "Team T-FORCE VULCAN Z 32GB (2 x 16GB) DDR4 3200", False),  # one stick of it
    ("8GB DDR4-3200", "Team T-FORCE VULCAN Z 32GB (2 x 16GB) DDR4 3200", True),
    ("2x16GB DDR4", "16GB (2x8GB) DDR4", True),                                    # two different kits
    ("2x8GB DDR4", "XPG 16GB DDR4-2400", False),
    ("16GB DDR4 2400 Non-ECC", ATECH, True),                                        # 16GB vs an 8GB stick
])
def test_capacity_conflicts(query, candidate, conflict):
    assert ("capacity" in ram_spec_conflicts(query, candidate)) is conflict
