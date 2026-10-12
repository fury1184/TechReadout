"""v3.8.7 regression: RAM capacity must never be canonicalized away.

Import Specs skipped "Samsung 8GB PC3-14900R" as a duplicate of
"Samsung 16GB 2Rx4 PC3-14900R" because PC3-14900R was extracted as a
part number and matched at score 100.
"""
from types import SimpleNamespace

import pytest

from app.name_normalization import (
    choose_existing_canonical_name,
    extract_part_number,
    extract_ram_capacity,
)


def _spec(manufacturer, model):
    return SimpleNamespace(manufacturer=manufacturer, model=model)


@pytest.mark.parametrize("model", [
    "8GB PC3-14900R",
    "16GB 2Rx4 PC3-14900R",
    "8GB PC3L-12800R",
    "4GB PC3-10600E",
    "16GB PC4-2400T-R",
    "32GB PC4-2666V-RB2",
    "8GB PC3-12800R-11-12-E2",
    "8GB DDR3L-1600",
    "16GB DDR4-3200R",
])
def test_jedec_ratings_are_not_part_numbers(model):
    assert extract_part_number(model) is None


def test_real_part_numbers_still_extracted():
    assert extract_part_number("16GB M393B2G70QH0-CMA") == "M393B2G70QH0-CMA"
    assert extract_part_number("Kingston KVR16R11D4/16") == "KVR16R11D4/16"


@pytest.mark.parametrize("model,expected", [
    ("8GB PC3-14900R", (1, 8)),
    ("16GB 2Rx4 PC3-14900R", (1, 16)),
    ("2x8GB DDR4-3200", (2, 8)),
    ("2 × 16 GB DDR4-3600", (2, 16)),
    ("PC3-14900R", None),
])
def test_extract_ram_capacity(model, expected):
    assert extract_ram_capacity(model) == expected


def test_8gb_not_canonicalized_to_16gb():
    existing = [_spec("Samsung", "16GB 2Rx4 PC3-14900R")]
    assert choose_existing_canonical_name(
        "Samsung", "8GB PC3-14900R", "RAM", existing
    ) == (None, None)


def test_same_part_number_different_capacity_rejected():
    existing = [_spec("Samsung", "16GB M393B2G70QH0-CMA")]
    assert choose_existing_canonical_name(
        "Samsung", "8GB M393B2G70QH0-CMA", "RAM", existing
    ) == (None, None)


def test_kit_not_canonicalized_to_single_stick():
    existing = [_spec("Corsair", "16GB CMK16GX4M1E3200C16")]
    assert choose_existing_canonical_name(
        "Corsair", "2x8GB CMK16GX4M1E3200C16", "RAM", existing
    ) == (None, None)


def test_same_capacity_same_part_still_canonicalizes():
    existing = [_spec("Samsung", "16GB M393B2G70QH0-CMA")]
    assert choose_existing_canonical_name(
        "Samsung", "Samsung 16GB M393B2G70QH0-CMA Server Memory", "RAM", existing
    ) == ("Samsung", "16GB M393B2G70QH0-CMA")


def test_capacity_unknown_on_one_side_does_not_block():
    existing = [_spec("Samsung", "M393B2G70QH0-CMA")]
    assert choose_existing_canonical_name(
        "Samsung", "16GB M393B2G70QH0-CMA", "RAM", existing
    ) == ("Samsung", "M393B2G70QH0-CMA")
