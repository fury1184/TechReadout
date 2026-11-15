"""Optional single-password login.

Set APP_PASSWORD (env var or create_app config) to require a password for
every page. When it is unset or empty, the app is open exactly as before.
There is one shared password and no user accounts.
"""
import hmac
import time
from urllib.parse import urlparse

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)

bp = Blueprint('auth', __name__)

# Endpoints reachable without logging in.
_PUBLIC_ENDPOINTS = {'auth.login', 'static'}


def auth_enabled():
    return bool(current_app.config.get('APP_PASSWORD'))


def password_matches(candidate):
    expected = current_app.config.get('APP_PASSWORD') or ''
    return hmac.compare_digest(candidate.encode('utf-8'), expected.encode('utf-8'))


def _safe_next(target):
    """Only follow same-site relative paths after login (no open redirect)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith('/') or target.startswith('//'):
        return None
    return target


def require_login():
    """before_request hook: block everything but the login page when logged out."""
    if not auth_enabled() or request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if session.get('authenticated'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if not auth_enabled() or session.get('authenticated'):
        return redirect(_safe_next(request.args.get('next')) or url_for('main.dashboard'))

    error = None
    if request.method == 'POST':
        if password_matches(request.form.get('password', '')):
            session.clear()
            session['authenticated'] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get('next')) or url_for('main.dashboard'))
        time.sleep(1)  # slow down password guessing
        error = 'Incorrect password.'
    return render_template('login.html', error=error), (401 if error else 200)


@bp.route('/logout', methods=['POST', 'GET'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
