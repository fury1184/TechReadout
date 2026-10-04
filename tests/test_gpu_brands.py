"""Tests for app/scrapers/gpu_brands.py — the single shared source of GPU
chip-vendor vs AIB board-partner data (v3.8.10)."""

from app.scrapers.gpu_brands import (
    detect_gpu_chip_vendor,
    extract_board_partner,
    strip_aib_suffix,
    GPU_BOARD_PARTNERS,
)


class TestDetectGpuChipVendor:
    def test_nvidia_from_geforce(self):
        assert detect_gpu_chip_vendor("EVGA GeForce GTX 1660 Ti SC Ultra") == "NVIDIA"

    def test_amd_from_radeon(self):
        assert detect_gpu_chip_vendor("Sapphire Radeon RX 6800 XT Nitro+") == "AMD"

    def test_intel_from_arc(self):
        assert detect_gpu_chip_vendor("Intel Arc A770") == "Intel"

    def test_falls_back_to_model_family_when_brand_word_absent(self):
        # Plenty of real titles never say "NVIDIA"/"GeForce" at all.
        assert detect_gpu_chip_vendor("MSI GTX 1080 Aero 8G") == "NVIDIA"
        assert detect_gpu_chip_vendor("XFX RX 6700 XT") == "AMD"

    def test_no_chip_word_or_model_family_returns_none(self):
        assert detect_gpu_chip_vendor("MSI Gaming X Trio") is None
        assert detect_gpu_chip_vendor("") is None
        assert detect_gpu_chip_vendor(None) is None


class TestExtractBoardPartner:
    def test_finds_known_partner(self):
        assert extract_board_partner("EVGA GeForce GTX 1660 Ti SC Ultra") == "EVGA"

    def test_case_insensitive_and_proper_cased_result(self):
        assert extract_board_partner("msi gtx 1080 aero 8g") == "MSI"

    def test_no_partner_in_text_returns_none(self):
        assert extract_board_partner("GeForce RTX 3060 12GB") is None
        assert extract_board_partner("") is None

    def test_word_boundary_avoids_false_match(self):
        # "asus" should not match inside an unrelated longer token.
        assert extract_board_partner("pegasus graphics dock") is None

    def test_every_board_partner_has_a_chip_mapping_or_is_explicitly_empty(self):
        # Guards against a future addition to GPU_BOARD_PARTNERS forgetting
        # its chip-vendor set (scoring.py's _board_partner_of() depends on it).
        for brand, chips in GPU_BOARD_PARTNERS.items():
            assert isinstance(chips, set)


class TestStripAibSuffix:
    def test_strips_brand_prefix_and_capacity(self):
        assert strip_aib_suffix("GeForce RTX 3060 Ti") == "rtx 3060 ti"

    def test_strips_aib_variant(self):
        assert strip_aib_suffix("GeForce RTX 3060 XC Gaming 12GB") == "rtx 3060"
