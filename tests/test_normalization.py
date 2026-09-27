"""Tests for app/scrapers/normalization.py — pure string/query helpers."""

from app.scrapers.normalization import (
    normalize_model_name,
    extract_key_identifiers,
    normalize_gpu_query,
)


class TestNormalizeModelName:
    def test_strips_leading_brand_and_family_prefixes(self):
        # Both "intel" and "core" are stripped; separators removed.
        assert normalize_model_name("Intel Core i7-9700K") == "i79700k"

    def test_empty_input(self):
        assert normalize_model_name("") == ""
        assert normalize_model_name(None) == ""

    def test_removes_separators(self):
        assert normalize_model_name("RTX 3060") == "rtx3060"


class TestExtractKeyIdentifiers:
    def test_gpu_ti_suffix(self):
        assert "ti" in extract_key_identifiers("RTX 4070 Ti")

    def test_amd_xt_suffix(self):
        assert "xt" in extract_key_identifiers("RX 7900 XT")

    def test_version_and_cpu_suffix(self):
        ids = extract_key_identifiers("E5-2687W v4")
        assert "v4" in ids
        assert "w" in ids

    def test_ddr4_does_not_produce_false_revision(self):
        # The negative lookbehind must stop 'r4' in DDR4 registering as a revision.
        assert extract_key_identifiers("DDR4") == []


class TestNormalizeGpuQuery:
    def test_strips_aib_partner_and_suffix(self):
        assert normalize_gpu_query("EVGA GTX 1660 Ti SC Ultra") == "gtx 1660 ti"

    def test_plain_reference_name_unchanged(self):
        assert normalize_gpu_query("RTX 4090") == "rtx 4090"

    def test_returns_original_when_stripping_would_empty_it(self):
        # "MSI" (partner) + "Gaming" (suffix) strips to nothing -> keep original.
        assert normalize_gpu_query("MSI Gaming") == "MSI Gaming"
