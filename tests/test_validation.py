"""Tests for app/scrapers/validation.py — result validation & gating rules."""

from app.scrapers.validation import (
    is_known_value,
    coerce_unknowns_to_none,
    present_spec_fields,
    has_minimum_specs,
    missing_required_fields,
    validation_status,
    validate_result,
    acceptable_scrape_hit,
)


class TestIsKnownValue:
    def test_none_and_blank_are_unknown(self):
        assert is_known_value(None) is False
        assert is_known_value("") is False

    def test_unknown_markers(self):
        for marker in ("unknown", "N/A", "na", "none", "not specified"):
            assert is_known_value(marker) is False

    def test_real_values_are_known(self):
        assert is_known_value("DDR4") is True
        # Numeric zero must count as a real, known value (regression guard).
        assert is_known_value(0) is True


class TestCoerceUnknownsToNone:
    def test_normalizes_marker_strings(self):
        out = coerce_unknowns_to_none(
            {"a": "unknown", "b": "DDR4", "c": None, "d": "n/a"}
        )
        assert out == {"a": None, "b": "DDR4", "c": None, "d": None}

    def test_non_dict_passthrough(self):
        assert coerce_unknowns_to_none("nope") == "nope"


class TestPresentSpecFields:
    def test_only_known_gpu_fields_returned(self):
        result = {"gpu_memory_size": 12, "gpu_memory_type": "unknown", "gpu_tdp": None}
        assert present_spec_fields(result, "GPU") == ["gpu_memory_size"]


class TestHasMinimumSpecs:
    def test_structured_type_requires_a_real_field(self):
        assert has_minimum_specs({"model": "i7-9700K", "cpu_cores": 8}, "CPU") is True
        assert has_minimum_specs({"model": "i7-9700K"}, "CPU") is False

    def test_no_model_is_false(self):
        assert has_minimum_specs({"cpu_cores": 8}, "CPU") is False

    def test_accessory_type_needs_only_model(self):
        assert has_minimum_specs({"model": "Fractal Define 7"}, "Case") is True


class TestMissingRequiredFields:
    def test_reports_missing(self):
        missing = missing_required_fields({"manufacturer": "NVIDIA", "model": "RTX 3060"}, "GPU")
        assert "gpu_memory_size" in missing
        assert "gpu_memory_type" in missing

    def test_complete_gpu_has_none_missing(self):
        complete = {
            "manufacturer": "NVIDIA", "model": "RTX 3060",
            "gpu_memory_size": 12, "gpu_memory_type": "GDDR6",
        }
        assert missing_required_fields(complete, "GPU") == []


class TestValidationStatus:
    def test_shape(self):
        status = validation_status({"manufacturer": "NVIDIA", "model": "RTX 3060"}, "GPU")
        assert set(status) == {
            "has_minimum_specs", "missing_required_fields",
            "present_spec_fields", "is_incomplete", "needs_review",
        }
        assert status["needs_review"] is True  # missing required GPU fields


class TestValidateResult:
    def test_empty_result_model_fails(self):
        assert validate_result("RTX 3060", "") is False

    def test_gpu_exact_match(self):
        assert validate_result("RTX 3060", "RTX 3060", "GPU") is True

    def test_gpu_rejects_ti_variant(self):
        assert validate_result("RTX 4070", "RTX 4070 Ti", "GPU") is False

    def test_cpu_suffix_mismatch_rejected(self):
        assert validate_result("i7-9700", "i7-9700K", "CPU") is False

    def test_cpu_exact_suffix_match(self):
        assert validate_result("i7-9700K", "i7-9700K", "CPU") is True

    def test_cpu_model_number_mismatch_rejected(self):
        assert validate_result("E5-2687", "E5-2680", "CPU") is False


class TestAcceptableScrapeHit:
    def test_valid_hit(self):
        result = {
            "model": "i7-9700K", "manufacturer": "Intel",
            "cpu_cores": 8, "cpu_threads": 8, "cpu_socket": "LGA1151",
        }
        assert acceptable_scrape_hit("i7-9700K", result, "CPU") is True

    def test_unknown_specs_are_coerced_and_rejected(self):
        result = {"model": "i7-9700K", "cpu_socket": "unknown", "cpu_cores": "n/a"}
        assert acceptable_scrape_hit("i7-9700K", result, "CPU") is False
