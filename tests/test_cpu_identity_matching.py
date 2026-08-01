from app.scrapers.scoring import score_candidate
from app.scrapers.validation import cpu_models_compatible, extract_cpu_identity, validate_result


def test_xeon_format_variants_match():
    assert cpu_models_compatible('E5-2696 v4', 'Intel Xeon E5 2696 V4')
    assert extract_cpu_identity('Xeon E5-2687W v4') == ('e5', '2687', 'w', '4')


def test_nearby_xeons_are_rejected():
    assert not cpu_models_compatible('E5-2696 v4', 'Intel Xeon E5-2640 v4')
    assert not cpu_models_compatible('E5-2696 v4', 'Intel Xeon E5-2680 v4')
    assert not validate_result('E5-2696 v4', 'Intel Xeon E5-2699 v4', 'CPU')


def test_revision_and_suffix_must_match():
    assert not cpu_models_compatible('E5-2696 v4', 'E5-2696 v3')
    assert not cpu_models_compatible('i7-9700K', 'Intel Core i7-9700')


def test_mismatched_cpu_scores_zero():
    assert score_candidate('E5-2696 v4', 'Intel Xeon E5-2640 v4', 'Intel', 'CPU') == 0
