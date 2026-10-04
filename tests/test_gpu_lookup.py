"""v3.8.10: GPU lookups with a memory size, the wrong-type check, Retry anyway.

"msi gtx 1080 aero 8g" never reached the GTX 1080's known TechPowerUp page,
because the "8g" stayed in the link. Cards with memory versions (RTX 4060 Ti
8 GB / 16 GB) need the size to pick the page, and a result whose memory size
differs from the query must be rejected, not saved.
"""
import pytest
import requests

from app import db
from app.models import LookupCache
from app.scrapers import lookup
from app.scrapers.normalization import normalize_gpu_query
from app.scrapers.validation import acceptable_scrape_hit, validate_result


def direct(query):
    """The direct TechPowerUp link, built the way the lookup builds it."""
    return lookup.get_direct_tpu_url(normalize_gpu_query(query, log=False), 'GPU')


@pytest.mark.parametrize('query, page', [
    ('msi gtx 1080 aero 8g', 'geforce-gtx-1080.c2839'),        # one version: memory ignored
    ('rtx 4060 ti 8g', 'geforce-rtx-4060-ti-8-gb.c3890'),
    ('rtx 4060 ti 16gb', 'geforce-rtx-4060-ti-16-gb.c4155'),
    ('rtx 4060 ti', 'geforce-rtx-4060-ti.c3890'),               # no size: 8 GB, like the 5060 Ti entry
    ('rtx 3060 12gb', 'geforce-rtx-3060-12-gb.c3682'),
    ('rtx 5060 ti 16gb', 'geforce-rtx-5060-ti-16-gb.c4292'),
    ('rtx 5060 ti 8g', 'geforce-rtx-5060-ti-8-gb.c4246'),
])
def test_known_pages_are_reached(query, page):
    assert direct(query).endswith('/gpu-specs/' + page)


@pytest.mark.parametrize('query', ['rtx 4060 ti 12gb', 'rtx 3060 8gb'])
def test_unlisted_memory_size_does_not_fall_back_to_another_size(query):
    assert '.c' not in direct(query).split('/gpu-specs/')[-1]   # no known ID: skip TechPowerUp


# ── parse_amazon_gpu: manufacturer must be the chip vendor (v3.8.10) ────────
# Was: manufacturer saved as whatever AIB brand was in the title (e.g. "MSI"),
# which then landed on the shared HardwareSpec and showed up as a stray peer
# of NVIDIA/AMD in /stats "By Manufacturer". The AIB brand is still useful —
# it's just not what belongs on a *shared* reference spec — so it's now kept
# separately as board_manufacturer for the caller to offer as the per-unit
# Inventory manufacturer instead.

def _amazon_gpu_page(title):
    return f'<html><body><span id="productTitle">{title}</span></body></html>'


@pytest.mark.parametrize('title, chip_vendor, board_manufacturer', [
    ('EVGA GeForce GTX 1660 Ti SC Ultra Gaming, 6GB GDDR6', 'NVIDIA', 'EVGA'),
    ('MSI GTX 1080 Aero 8G OC', 'NVIDIA', 'MSI'),
    ('Sapphire Radeon RX 6800 XT Nitro+ 16GB', 'AMD', 'Sapphire'),
])
def test_manufacturer_is_chip_vendor_not_aib_brand(title, chip_vendor, board_manufacturer):
    specs = lookup.parse_amazon_gpu(_amazon_gpu_page(title), 'https://amazon.com/x')
    assert specs['manufacturer'] == chip_vendor
    assert specs['board_manufacturer'] == board_manufacturer


def test_no_recognizable_board_partner_omits_the_field():
    specs = lookup.parse_amazon_gpu(
        _amazon_gpu_page('NVIDIA GeForce RTX 4090 Founders Edition 24GB'),
        'https://amazon.com/x',
    )
    assert specs['manufacturer'] == 'NVIDIA'
    assert 'board_manufacturer' not in specs


@pytest.mark.parametrize('query, component_type', [
    ('rtx 4060 ti 12gb', 'GPU'),       # not on the known-pages list
    ('Xeon E5-1680 v4', 'CPU'),
])
def test_no_known_page_is_a_clean_miss_without_a_paid_call(flask_app, monkeypatch, query, component_type):
    """v3.8.11: TechPowerUp's ?q= search answers HTTP 410 now, so it isn't called."""
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup.requests, 'get', lambda *a, **k: pytest.fail('paid call made'))
    lookup._begin_lookup_budget()
    try:
        assert lookup.search_with_scrapedo(query, component_type) is None
        assert lookup.lookup_problems() == []
    finally:
        lookup._end_lookup_budget()


@pytest.mark.parametrize('query, name, ok', [
    ('rtx 4060 ti 16gb', 'NVIDIA GeForce RTX 4060 Ti 8 GB', False),
    ('rtx 4060 ti 16gb', 'NVIDIA GeForce RTX 4060 Ti 16 GB', True),
    ('rtx 4060 ti 8g', 'MSI GeForce RTX 4060 Ti Ventus 2X 16G OC', False),
    ('rtx 4060 ti', 'NVIDIA GeForce RTX 4060 Ti 8 GB', True),           # no size asked for
    ('msi gtx 1080 aero 8g', 'NVIDIA GeForce GTX 1080', True),           # name states no size
])
def test_title_check_compares_memory(query, name, ok):
    assert validate_result(query, name, 'GPU', log=False) is ok


def test_final_check_compares_parsed_memory():
    page_8gb = {'model': 'NVIDIA GeForce RTX 4060 Ti', 'gpu_memory_size': 8192, 'gpu_boost_clock': 2535}
    assert acceptable_scrape_hit('rtx 4060 ti 16gb', dict(page_8gb), 'GPU') is False
    gtx_1080 = {'model': 'NVIDIA GeForce GTX 1080', 'gpu_memory_size': 8192, 'gpu_boost_clock': 1733}
    assert acceptable_scrape_hit('msi gtx 1080 aero 8g', dict(gtx_1080), 'GPU') is True


@pytest.mark.parametrize('query, selected, conflict', [
    ('msi gtx 1080 aero 8g', 'CPU', 'GPU'),
    ('Radeon RX 580 8GB', 'CPU', 'GPU'),
    ('Intel Core i7-6700', 'GPU', 'CPU'),
    ('Xeon E5-2680 v4', 'GPU', 'CPU'),
    ('AMD Ryzen 7 5700G with Radeon Graphics', 'CPU', None),   # APU: names both
    ('rtx 3060', 'GPU', None),
    ('msi gtx 1080', 'auto', None),
])
def test_obvious_type_conflict(query, selected, conflict):
    assert lookup.obvious_type_conflict(query, selected) == conflict


def test_wrong_type_stops_before_any_lookup(client, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError('the paid lookup ran')
    monkeypatch.setattr(lookup, 'lookup_hardware', must_not_run)
    data = client.post('/api/lookup', json={'query': 'msi gtx 1080 aero 8g', 'component_type': 'CPU'}).get_json()
    assert data['type_mismatch'] == 'GPU' and 'Set the type to GPU' in data['message']


def test_retry_anyway_skips_the_saved_not_found(client, monkeypatch):
    calls = []
    monkeypatch.setattr(lookup, 'lookup_hardware', lambda *a, **k: calls.append(1))
    body = {'query': 'Nonexistent GT 9999', 'component_type': 'GPU'}
    client.post('/api/lookup', json=body)                                  # clean miss, saved
    saved = client.post('/api/lookup', json=body).get_json()               # answered from it
    assert saved['cached_miss'] is True and saved['cached_on'] and len(calls) == 1
    client.post('/api/lookup', json={**body, 'force_refresh': True})      # Retry anyway
    assert len(calls) == 2


def test_gpu_normalizer_can_be_quiet(capsys):
    normalize_gpu_query('msi gtx 1080 aero 8g', log=False)
    assert capsys.readouterr().out == ''


def test_empty_pick_names_what_it_rejected(capsys):
    from tests.test_lookup_sources import AMAZON_SEARCH, page
    listings = lookup.parse_amazon_search_results(page(AMAZON_SEARCH))
    assert lookup.pick_listing('msi gtx 1080 aero 8g', 'GPU', listings, 'Amazon search') is None
    out = capsys.readouterr().out
    assert '0 passed validation' in out and 'first titles checked' in out


# ── v3.8.11 ────────────────────────────────────────────────────────────────
from app.models import AppSetting
from app.scrapers.scoring import score_candidate


@pytest.mark.parametrize('name, maker, expected', [
    ('NVIDIA GeForce GTX 1650 Super', 'NVIDIA', 90),     # EVGA builds NVIDIA cards
    ('NVIDIA GeForce GTX 1650', 'NVIDIA', 80),
    ('NVIDIA GeForce GTX 1660 Super', 'NVIDIA', 80),
])
def test_board_maker_matches_the_chip_maker(name, maker, expected):
    assert score_candidate('EVGA GTX 1650 Super', name, maker, 'GPU') == expected


def test_board_maker_does_not_match_the_other_chip_maker():
    assert score_candidate('Sapphire RX 580', 'NVIDIA GeForce GTX 1060', 'NVIDIA', 'GPU') < 50


def test_saved_reference_spec_is_used_without_a_paid_lookup(client, make_spec, monkeypatch):
    make_spec('GPU', 'NVIDIA', 'GeForce GTX 1650 Super', gpu_memory_size=4096)
    monkeypatch.setattr(lookup, 'lookup_hardware', lambda *a, **k: pytest.fail('paid lookup ran'))
    data = client.post('/api/lookup', json={'query': 'EVGA GTX 1650 Super', 'component_type': 'GPU'}).get_json()
    assert data['found'] is True and 'GTX 1650 Super' in data['model']


def _tpu_page(status, text):
    resp = requests.models.Response()
    resp.status_code, resp._content, resp.url = status, text.encode(), 'https://api.scrape.do?token=x'
    return resp


def test_known_page_without_specs_skips_the_techpowerup_search(flask_app, monkeypatch):
    calls = []
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup.requests, 'get', lambda url, timeout: calls.append(url) or _tpu_page(200, '<html>blocked</html>'))
    lookup._begin_lookup_budget()
    try:
        assert lookup.search_with_scrapedo('EVGA GTX 1650 Super', 'GPU') is None
        assert len(calls) == 1                          # no TechPowerUp search after it
        assert 'TechPowerUp returned no spec page' in lookup.lookup_problems()
    finally:
        lookup._end_lookup_budget()


def test_wrong_known_page_is_named_and_rejected(flask_app, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup.requests, 'get', lambda url, timeout: calls.append(url) or _tpu_page(200, '<div class="gpuname">x</div>'))
    monkeypatch.setattr(lookup, 'parse_techpowerup_detail', lambda html, ct, url: {
        'model': 'AMD Radeon Pro SSG', 'gpu_memory_size': 4096, 'source': 'techpowerup'})
    assert lookup.search_with_scrapedo('rtx 3060', 'GPU') is None
    out = capsys.readouterr().out
    assert "Known page for 'rtx 3060' is AMD Radeon Pro SSG" in out
    assert len(calls) == 1                              # no TechPowerUp search after it


def _budget_stop(monkeypatch):
    monkeypatch.setattr(lookup, 'lookup_hardware', lambda *a, **k: {'error': 'scrapedo_budget_exhausted'})


def test_budget_stop_shows_the_database_matches(client, make_spec, monkeypatch):
    make_spec('GPU', 'NVIDIA', 'GeForce GTX 1650', gpu_memory_size=4096)     # scores 80: needs review
    _budget_stop(monkeypatch)
    data = client.post('/api/lookup', json={'query': 'EVGA GTX 1650 Super', 'component_type': 'GPU'}).get_json()
    assert data['status'] == 'needs_review' and data['candidates']
    assert data['note'].startswith('Stopped after 3 paid calls (Normal)') and 'Thorough' in data['note']


def test_budget_stop_with_nothing_to_review_suggests_thorough(client, monkeypatch):
    _budget_stop(monkeypatch)
    data = client.post('/api/lookup', json={'query': 'EVGA GTX 1650 Super', 'component_type': 'GPU'}).get_json()
    assert data['scrapedo_budget_exhausted'] is True and 'set Paid lookup depth to Thorough' in data['message']


def test_budget_stop_on_thorough_has_no_higher_setting_to_suggest(client, monkeypatch):
    db.session.add(AppSetting(key='scrapedo_lookup_depth', value='thorough'))
    db.session.commit()
    _budget_stop(monkeypatch)
    data = client.post('/api/lookup', json={'query': 'EVGA GTX 1650 Super', 'component_type': 'GPU'}).get_json()
    assert data['message'] == 'Stopped after 5 paid calls (Thorough) before finishing.'


# ── TechPowerUp without render=true (v3.8.11) ──────────────────────────────
# Saved from real Scrape.Do responses for the GTX 1650 Super page: the headless
# browser (render=true) got a bot check, the plain fetch got the spec page.
from tests.test_lookup_sources import page as fixture

TPU_PLAIN = 'tpu_gtx_1650_super_plain.html.gz'
TPU_BOT_CHECK = 'tpu_bot_check_headless.html.gz'


def test_techpowerup_bot_check_is_recognized():
    assert lookup.looks_blocked(fixture(TPU_BOT_CHECK)) == 'TechPowerUp'
    assert lookup.looks_blocked(fixture(TPU_PLAIN)) is None


def test_techpowerup_page_is_parsed_with_its_current_heading():
    result = lookup.parse_techpowerup_detail(fixture(TPU_PLAIN), 'GPU', 'https://www.techpowerup.com/gpu-specs/geforce-gtx-1650-super.c3411')
    assert (result['model'], result['manufacturer']) == ('NVIDIA GeForce GTX 1650 SUPER', 'NVIDIA')
    assert (result['gpu_memory_size'], result['gpu_memory_type'], result['gpu_tdp']) == (4096, 'GDDR6', 100)


def _serve(monkeypatch, name, calls):
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup.requests, 'get', lambda url, timeout: calls.append(url) or _tpu_page(200, fixture(name)))


def test_known_page_is_fetched_without_render_and_accepted(flask_app, monkeypatch):
    calls = []
    _serve(monkeypatch, TPU_PLAIN, calls)
    monkeypatch.setenv('SCRAPEDO_TOKEN', 'x')
    result = lookup.lookup_hardware('EVGA GTX 1650 Super', 'GPU')
    assert result['source'] == 'techpowerup' and result['model'] == 'NVIDIA GeForce GTX 1650 SUPER'
    assert len(calls) == 1 and 'render' not in calls[0]


def test_bot_check_on_the_known_page_is_logged_as_a_problem(flask_app, monkeypatch):
    calls = []
    _serve(monkeypatch, TPU_BOT_CHECK, calls)
    lookup._begin_lookup_budget()
    try:
        assert lookup.search_with_scrapedo('EVGA GTX 1650 Super', 'GPU') is None
        assert len(calls) == 1
        assert lookup.lookup_problems() == ['TechPowerUp returned a bot-check page']
    finally:
        lookup._end_lookup_budget()


def test_wrong_rtx_4060_entry_is_gone():
    assert '.c' not in direct('rtx 4060').split('/gpu-specs/')[-1]


# ── v3.8.11 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('status, paid_calls', [(404, 0), (410, 0), (403, 1)])
def test_cpu_monkey_pays_for_a_retry_only_when_blocked(flask_app, monkeypatch, status, paid_calls):
    """A 404 means CPU-Monkey doesn't list the CPU (e.g. Xeon E5-1680 v4)."""
    calls = []
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup, 'scrapedo_fallback_enabled', lambda: True)
    monkeypatch.setattr(lookup.requests, 'get', lambda url, **k: calls.append(url) or _tpu_page(status, 'nope'))
    assert lookup.search_cpu_monkey('Xeon E5-1680 v4') is None
    assert sum('api.scrape.do' in url for url in calls) == paid_calls
