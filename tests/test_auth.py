"""Optional single-password login (app/auth.py)."""
import pytest

from app import create_app, db


@pytest.fixture
def locked_client():
    app = create_app({
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'TESTING': True,
        'APP_PASSWORD': 's3cret',
    })
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_open_when_no_password_set(client):
    assert client.get('/').status_code != 302


def test_pages_redirect_to_login(locked_client):
    resp = locked_client.get('/inventory')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_api_returns_401(locked_client):
    assert locked_client.get('/api/duplicates').status_code == 401


def test_login_page_is_public(locked_client):
    assert locked_client.get('/login').status_code == 200


def test_wrong_password_rejected(locked_client, monkeypatch):
    monkeypatch.setattr('app.auth.time.sleep', lambda s: None)
    resp = locked_client.post('/login', data={'password': 'nope'})
    assert resp.status_code == 401
    assert locked_client.get('/').status_code == 302


def test_correct_password_logs_in_and_out(locked_client):
    resp = locked_client.post('/login?next=/inventory', data={'password': 's3cret'})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/inventory')
    assert locked_client.get('/').status_code != 302
    locked_client.get('/logout')
    assert locked_client.get('/').status_code == 302


@pytest.mark.parametrize('target', ['https://evil.example/', '//evil.example/', 'javascript:alert(1)'])
def test_next_cannot_leave_site(locked_client, target):
    resp = locked_client.post('/login', query_string={'next': target}, data={'password': 's3cret'})
    assert resp.status_code == 302
    assert 'evil' not in resp.headers['Location'] and 'javascript' not in resp.headers['Location']
