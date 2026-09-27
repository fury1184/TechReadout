"""v3.8.9: lookups no longer go through Google.

Google now answers scrapers with a JavaScript-only page, so every lookup path
that found its product page through a Google search came back empty. These
tests run the replacements (Amazon's own search, Newegg's embedded data, the
bot-check detector) against pages saved from real Scrape.Do responses in
tests/fixtures/, so they cost no credits.
"""
import gzip
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

from app import db
from app.models import LookupCache
from app.scrapers import lookup

FIXTURES = Path(__file__).parent / 'fixtures'
AMAZON_CAPTCHA = ('<html><body><form method="get" action="/errors/validateCaptcha">'
                  '<h4>Enter the characters you see below</h4></form></body></html>')


def page(name):
    with gzip.open(FIXTURES / name, 'rt', encoding='utf-8', errors='replace') as f:
        return f.read()


AMAZON_SEARCH = 'amazon_search_adata_xpg_ddr4_2400.html.gz'
NEWEGG_PRODUCT = 'newegg_ram_product_teamgroup_vulcan_z.html.gz'
NEWEGG_ZERO = 'newegg_search_zero_results.html.gz'
GOOGLE_JS = 'google_javascript_page.html.gz'


# ── Bot-check detection ────────────────────────────────────────────────────

def test_google_javascript_page_is_detected():
    assert lookup.looks_blocked(page(GOOGLE_JS)) == 'Google'


def test_amazon_robot_check_is_detected():
    assert lookup.looks_blocked(AMAZON_CAPTCHA) == 'Amazon'


@pytest.mark.parametrize('name', [AMAZON_SEARCH, NEWEGG_PRODUCT, NEWEGG_ZERO])
def test_normal_pages_are_not_flagged(name):
    assert lookup.looks_blocked(page(name)) is None


# ── Amazon search ──────────────────────────────────────────────────────────

def test_amazon_listings_skip_sponsored_and_keep_full_titles():
    listings = lookup.parse_amazon_search_results(page(AMAZON_SEARCH))
    asins = {entry['asin'] for entry in listings}
    assert len(listings) == 16
    # Sponsored carousels sit outside the result cards and are never listed.
    assert not asins & {'B07KGN82CZ', 'B09477S96W', 'B094WBGZLP', 'B08KSGVZDZ'}
    z1 = next(entry for entry in listings if entry['asin'] == 'B06XYSSS5C')
    assert z1['title'].startswith('XPG Z1 DDR4 2400 MHz')  # brand h2 alone is just "XPG"
    assert z1['url'] == 'https://www.amazon.com/dp/B06XYSSS5C'


def test_picker_takes_the_listing_that_matches_not_the_first():
    listings = lookup.parse_amazon_search_results(page(AMAZON_SEARCH))
    assert listings[0]['asin'] != 'B06XYSSS5C'            # first result is a DDR4-3200 kit
    pick = lookup.pick_listing('ADATA XPG DDR4-2400 CL16', 'RAM', listings, 'Amazon search')
    assert pick['asin'] == 'B06XYSSS5C'                    # XPG Z1 DDR4-2400


def test_picker_returns_none_when_nothing_matches():
    listings = lookup.parse_amazon_search_results(page(AMAZON_SEARCH))
    assert lookup.pick_listing('Corsair Vengeance LPX 16GB DDR4-3600', 'RAM', listings, 'Amazon search') is None


# ── Newegg ─────────────────────────────────────────────────────────────────

def test_newegg_embedded_data_is_decoded():
    state = lookup.newegg_initial_state(page(NEWEGG_ZERO))
    assert state['TotalItemCount'] == 0 and not state['Products']


def test_newegg_ram_product_page():
    result = lookup.parse_newegg_ram(page(NEWEGG_PRODUCT), 'https://www.newegg.com/p/N82E16820331618')
    assert result['model'].startswith('Team T-FORCE VULCAN Z 32GB (2 x 16GB)')
    assert result['manufacturer'] == 'Team Group'
    assert {k: result[k] for k in ('ram_size', 'ram_modules', 'ram_type', 'ram_speed',
                                   'ram_cas_latency', 'ram_ecc', 'ram_module_type')} == {
        'ram_size': 32, 'ram_modules': 2, 'ram_type': 'DDR4', 'ram_speed': 3200,
        'ram_cas_latency': 'CL16', 'ram_ecc': False, 'ram_module_type': 'UDIMM',
    }


@pytest.mark.parametrize('type_text, buffered, expected', [
    ('288-Pin PC RAM', 'Unbuffered', 'UDIMM'),
    ('288-Pin DDR4 SDRAM', 'Registered', 'RDIMM'),
    ('288-Pin DDR4 SDRAM', 'Load Reduced', 'LRDIMM'),
    ('260-Pin DDR4 SO-DIMM', 'Unbuffered', 'SODIMM'),
    ('', '', None),
])
def test_newegg_module_type(type_text, buffered, expected):
    assert lookup._newegg_module_type(type_text, buffered) == expected


# ── Problems stop a lookup from counting as "not found" ────────────────────

def _response(status, text='', cost='1'):
    resp = requests.models.Response()
    resp.status_code, resp._content, resp.url = status, text.encode(), 'https://api.scrape.do?token=x'
    resp.headers['Scrape.do-Request-Cost'] = cost
    return resp


def test_scrapedo_errors_are_recorded(monkeypatch):
    monkeypatch.setattr(lookup.requests, 'get', lambda url, timeout: _response(503))
    lookup._begin_lookup_budget()
    try:
        lookup.scrapedo_get('https://api.scrape.do?token=x&url=y')
        assert lookup.lookup_problems() == ['Scrape.Do HTTP 503']
    finally:
        lookup._end_lookup_budget()


def test_bot_check_makes_the_lookup_incomplete(flask_app, monkeypatch):
    monkeypatch.setenv('SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup.requests, 'get', lambda url, timeout: _response(200, AMAZON_CAPTCHA))
    result = lookup.lookup_hardware('ADATA XPG DDR4-2400 CL16', 'RAM')
    assert result == {'error': 'lookup_incomplete', 'problems': ['Amazon returned a bot-check page']}


def _cache_rows():
    return db.session.query(LookupCache).filter_by(status='miss').count()


def test_incomplete_lookup_is_not_saved_as_a_miss(client, monkeypatch):
    monkeypatch.setattr(lookup, 'lookup_hardware', lambda *a, **k: {
        'error': 'lookup_incomplete', 'problems': ['Amazon returned a bot-check page']})
    data = client.post('/api/lookup', json={'query': 'ADATA XPG DDR4-2400 CL16', 'component_type': 'RAM'}).get_json()
    assert data['lookup_incomplete'] is True and 'bot-check' in data['message']
    assert _cache_rows() == 0


def test_clean_miss_is_saved_and_expires_after_seven_days(client, monkeypatch):
    calls = []
    monkeypatch.setattr(lookup, 'lookup_hardware', lambda *a, **k: calls.append(1))
    body = {'query': 'Nonexistent 64GB DDR4-2400', 'component_type': 'RAM'}

    client.post('/api/lookup', json=body)
    client.post('/api/lookup', json=body)            # answered from the saved miss
    assert _cache_rows() == 1 and len(calls) == 1

    db.session.query(LookupCache).update({'updated_at': datetime.utcnow() - timedelta(days=8)})
    db.session.commit()
    client.post('/api/lookup', json=body)            # older than 7 days: runs again
    assert len(calls) == 2


# ── Newegg search and the RAM chain (Newegg, then Amazon) ──────────────────

NEWEGG_RESULTS = 'newegg_search_teamgroup_vulcan_z.html.gz'
AMAZON_PRODUCT = 'amazon_product_xpg_z1_ddr4_2400.html.gz'


def test_newegg_results_come_from_the_product_list_not_ad_links():
    assert lookup.parse_newegg_search_results(page(NEWEGG_ZERO)) == []   # only a sponsored-brand ad
    [listing] = lookup.parse_newegg_search_results(page(NEWEGG_RESULTS))
    assert listing['title'].startswith('Team T-FORCE VULCAN Z 32GB (2 x 16GB)')
    assert listing['url'].endswith('/p/N82E16820331618')


def _scrapedo_router(pages, calls):
    """Fake requests.get for Scrape.Do: pick a saved page by the target URL."""
    def fake_get(url, timeout):
        calls.append(url)
        for marker, name in pages.items():
            if marker in url:
                return _response(200, page(name))
        raise AssertionError(f'unexpected fetch: {url}')
    return fake_get


@pytest.fixture
def scrapedo_on(flask_app, monkeypatch):
    monkeypatch.setenv('SCRAPEDO_TOKEN', 'x')
    monkeypatch.setattr(lookup, 'SCRAPEDO_TOKEN', 'x')
    return monkeypatch


def test_ram_found_on_newegg_costs_two_calls(scrapedo_on):
    calls = []
    scrapedo_on.setattr(lookup.requests, 'get', _scrapedo_router({
        'www.newegg.com/p/pl': NEWEGG_RESULTS, '/p/N82E16820331618': NEWEGG_PRODUCT}, calls))
    result = lookup.lookup_hardware('Team T-FORCE VULCAN Z DDR4-3200 CL16', 'RAM')
    assert result['source'] == 'newegg' and result['ram_speed'] == 3200
    assert len(calls) == 2


def test_ram_falls_back_to_amazon_when_newegg_has_nothing(scrapedo_on):
    calls = []
    scrapedo_on.setattr(lookup.requests, 'get', _scrapedo_router({
        'www.newegg.com/p/pl': NEWEGG_ZERO,
        'www.amazon.com/s%3Fk': AMAZON_SEARCH,
        'www.amazon.com/dp/B06XYSSS5C': AMAZON_PRODUCT}, calls))
    result = lookup.lookup_hardware('ADATA XPG DDR4-2400 CL16', 'RAM')
    assert result['source'] == 'amazon'
    assert result['model'].startswith('XPG Z1 DDR4 2400 MHz')
    assert (result['ram_type'], result['ram_speed'], result['ram_cas_latency']) == ('DDR4', 2400, 'CL16')
    assert len(calls) == 3        # Newegg search, Amazon search, Amazon product


# ── Motherboard Newegg search and parser ───────────────────────────────────

def test_motherboard_search_does_not_open_the_ad_when_newegg_has_no_match(scrapedo_on):
    calls = []
    scrapedo_on.setattr(lookup.requests, 'get', _scrapedo_router({'www.newegg.com/p/pl': NEWEGG_ZERO}, calls))
    assert lookup.search_motherboard_newegg('ASUS PRIME B450M-A II') is None
    assert len(calls) == 1        # the sponsored Team Group product is never fetched


def test_newegg_product_parser_reads_the_embedded_data(capsys):
    # Same Newegg product-page template as motherboards; the JSON step used to
    # fail with "Extra data" and fall back to the spec table.
    result = lookup.parse_newegg_motherboard(page(NEWEGG_PRODUCT), 'https://www.newegg.com/p/N82E16820331618')
    assert 'falling back' not in capsys.readouterr().out
    assert result['model'].startswith('Team T-FORCE VULCAN Z 32GB (2 x 16GB)')
    assert result['raw_data']['CAS Latency'] == 'CL16'


# ── Amazon RAM specs come from the product's own table and title ───────────

def test_amazon_ram_specs_ignore_other_products_on_the_page():
    result = lookup.parse_amazon_generic(page(AMAZON_PRODUCT), 'https://www.amazon.com/dp/B06XYSSS5C', 'RAM')
    # The page also shows a Kingston DDR5 kit and an A-Tech CL17 stick.
    assert {k: result.get(k) for k in ('manufacturer', 'ram_type', 'ram_speed', 'ram_cas_latency',
                                       'ram_size', 'ram_modules')} == {
        'manufacturer': 'XPG', 'ram_type': 'DDR4', 'ram_speed': 2400,
        'ram_cas_latency': 'CL16', 'ram_size': 16, 'ram_modules': 2,
    }


def test_kit_total_in_the_query_finds_the_kit_on_newegg(scrapedo_on):
    calls = []
    scrapedo_on.setattr(lookup.requests, 'get', _scrapedo_router({
        'www.newegg.com/p/pl': NEWEGG_RESULTS, '/p/N82E16820331618': NEWEGG_PRODUCT}, calls))
    result = lookup.lookup_hardware('Team T-FORCE VULCAN Z 32GB DDR4-3200', 'RAM')
    assert result['source'] == 'newegg' and (result['ram_size'], result['ram_modules']) == (32, 2)
