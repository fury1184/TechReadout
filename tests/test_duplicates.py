"""Tests for the pure (DB-free) duplicate-scoring helpers in app/duplicates.py.

The query functions (find_duplicates etc.) need a DB session and are not covered
here; these tests exercise the normalization and scoring math they rely on.
"""

from app.duplicates import (
    normalize_duplicate_text,
    compact_duplicate_key,
    duplicate_score,
    _match_level,
)


class TestNormalizeDuplicateText:
    def test_strips_common_words(self):
        assert normalize_duplicate_text("Intel Core i7-9700K") == "i7 9700k"

    def test_collapses_ram_kit_notation(self):
        assert normalize_duplicate_text("16GB (2x8GB)") == "16gb 2x8gb"

    def test_blank(self):
        assert normalize_duplicate_text(None) == ""


class TestCompactDuplicateKey:
    def test_removes_all_separators(self):
        assert compact_duplicate_key("RTX 3060") == "rtx3060"


class TestDuplicateScore:
    def test_identical_is_max(self):
        assert duplicate_score("NVIDIA", "RTX 3060", "NVIDIA", "RTX 3060") == 100

    def test_substring_is_strong_match(self):
        score = duplicate_score("Samsung", "970 EVO", "Samsung", "970 EVO Plus 1TB")
        assert score >= 90

    def test_unrelated_is_low(self):
        score = duplicate_score("Intel", "i7-9700K", "AMD", "RX 6800 XT")
        assert score < 74  # below the "possible" threshold

    def test_bounded_0_100(self):
        assert 0 <= duplicate_score("A", "x", "B", "y") <= 100


class TestMatchLevel:
    def test_thresholds(self):
        assert _match_level(97) == "exact"
        assert _match_level(90) == "likely"
        assert _match_level(80) == "possible"
        assert _match_level(50) == "none"
