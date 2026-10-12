"""Scrape.Do token stays out of logs and error responses; balance check (v3.8.9)."""
import pytest
import requests

from app.scrapers import lookup

TOKEN = 'SECRET123'
API_URL = f'https://api.scrape.do?token={TOKEN}&render=true&url=https%3A//www.techpowerup.com/'


def test_redact_token_masks_only_the_token():
    assert lookup.redact_token(API_URL) == (
        'https://api.scrape.do?token=***&render=true&url=https%3A//www.techpowerup.com/'
    )


def test_network_error_is_reraised_without_the_token(monkeypatch):
    def fail(url, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: /?token={TOKEN}&url=x")
    monkeypatch.setattr(lookup.requests, 'get', fail)

    with pytest.raises(requests.ConnectionError) as info:
        lookup.scrapedo_get(API_URL)
    assert TOKEN not in str(info.value)
    assert info.value.__suppress_context__  # original exception hidden from tracebacks


def test_raise_for_status_message_has_no_token(monkeypatch):
    def bad_gateway(url, timeout):
        resp = requests.models.Response()
        resp.status_code, resp.reason, resp.url = 502, 'Bad Gateway', url
        return resp
    monkeypatch.setattr(lookup.requests, 'get', bad_gateway)

    resp = lookup.scrapedo_get(API_URL)
    with pytest.raises(requests.HTTPError) as info:
        resp.raise_for_status()
    assert TOKEN not in str(info.value)


class _InfoResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_credits_reads_the_usage_endpoint(client, monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _InfoResponse(200, {'IsActive': True, 'MaxMonthlyRequest': 250000,
                                   'RemainingMonthlyRequest': 187432})
    monkeypatch.setenv('SCRAPEDO_TOKEN', TOKEN)
    monkeypatch.setattr(requests, 'get', fake_get)

    data = client.get('/api/credits').get_json()
    assert calls == [('https://api.scrape.do/info', {'token': TOKEN})]
    assert data == {'remaining': 187432, 'monthly_limit': 250000, 'active': True, 'reset_date': 'Monthly'}


def test_credits_rate_limit_message(client, monkeypatch):
    monkeypatch.setenv('SCRAPEDO_TOKEN', TOKEN)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _InfoResponse(429))
    assert '10 balance checks a minute' in client.get('/api/credits').get_json()['error']


def test_credits_error_never_returns_the_token(client, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: /info?token={TOKEN}")
    monkeypatch.setenv('SCRAPEDO_TOKEN', TOKEN)
    monkeypatch.setattr(requests, 'get', fail)
    body = client.get('/api/credits').get_data(as_text=True)
    assert TOKEN not in body
