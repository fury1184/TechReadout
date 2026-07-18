"""Tests for app/scrapers/scoring.py — candidate confidence scoring."""

from app.scrapers.scoring import (
    extract_vram_gb,
    strip_aib_suffix,
    score_candidate,
    spec_completeness_score,
    source_display_name,
    enrich_scrape_result,
    SOURCE_TRUST,
)


class TestExtractVramGb:
    def test_reads_gb_value(self):
        assert extract_vram_gb("RTX 3060 12GB") == 12
        assert extract_vram_gb("8 GB") == 8

    def test_missing_returns_none(self):
        assert extract_vram_gb("RTX 3060") is None
        assert extract_vram_gb("") is None


class TestStripAibSuffix:
    def test_strips_brand_prefix_and_capacity(self):
        assert strip_aib_suffix("GeForce RTX 3060 Ti") == "rtx 3060 ti"

    def test_strips_aib_variant(self):
        # "xc gaming" is a known AIB suffix; "12gb" is capacity.
        assert strip_aib_suffix("GeForce RTX 3060 XC Gaming 12GB") == "rtx 3060"


class TestScoreCandidate:
    def test_exact_match_scores_100(self):
        assert score_candidate("RTX 3060", "RTX 3060", "NVIDIA", "GPU") == 100

    def test_wrong_vram_scores_lower_than_correct(self):
        wrong = score_candidate("RTX 3060 8GB", "RTX 3060 12GB", "NVIDIA", "GPU")
        correct = score_candidate("RTX 3060 12GB", "RTX 3060 12GB", "NVIDIA", "GPU")
        assert wrong == 80
        assert correct == 100
        assert wrong < correct

    def test_clearly_unrelated_scores_low(self):
        score = score_candidate("NVIDIA RTX 3060 12GB", "Radeon RX 6800 XT", "AMD", "GPU")
        assert score < 40

    def test_never_exceeds_100(self):
        assert score_candidate("RTX 3060 12GB", "RTX 3060 12GB", "NVIDIA", "GPU") <= 100


class TestSpecCompletenessScore:
    def test_partial_gpu_specs(self):
        result = {"gpu_memory_size": 12, "gpu_memory_type": "GDDR6"}
        # 2 present of 5 target -> 40
        assert spec_completeness_score(result, "GPU") == 40

    def test_unknown_type_with_no_fields(self):
        assert spec_completeness_score({}, "GPU") == 0


class TestSourceDisplayName:
    def test_known_source(self):
        assert source_display_name("techpowerup") == "TechPowerUp"

    def test_unknown_source_passthrough(self):
        assert source_display_name("mystery") == "mystery"

    def test_empty_source(self):
        assert source_display_name("") == "Unknown"


class TestEnrichScrapeResult:
    def _full_gpu(self, source):
        return {
            "source": source,
            "model": "RTX 3060",
            "manufacturer": "NVIDIA",
            "gpu_memory_size": 12,
            "gpu_memory_type": "GDDR6",
            "gpu_base_clock": 1320,
            "gpu_boost_clock": 1777,
            "gpu_tdp": 170,
            "gpu_bus_interface": "PCIe 4.0",
        }

    def test_none_input_returns_none(self):
        assert enrich_scrape_result("RTX 3060", None, "GPU") is None

    def test_attaches_metadata_and_confidence(self):
        out = enrich_scrape_result("RTX 3060", self._full_gpu("techpowerup"), "GPU")
        assert "confidence" in out
        assert out["source_name"] == "TechPowerUp"
        assert out["raw_data"]["_lookup_metadata"]["source_trust"] == SOURCE_TRUST["techpowerup"]

    def test_openwebui_confidence_capped_at_89(self):
        out = enrich_scrape_result("RTX 3060", self._full_gpu("openwebui"), "GPU")
        assert out["confidence"] <= 89

    def test_missing_specs_caps_confidence_at_69(self):
        thin = {"source": "techpowerup", "model": "RTX 3060", "manufacturer": "NVIDIA"}
        out = enrich_scrape_result("RTX 3060", thin, "GPU")
        assert out["confidence"] <= 69
