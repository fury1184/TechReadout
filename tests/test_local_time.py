"""Stored UTC timestamps are shown in the local time zone ($TZ), v3.8.10.

An item added at 9:30 PM Eastern is stored as 01:30 UTC the next day, and the
dashboard used to show that next day's date.
"""
from datetime import date, datetime
from decimal import Decimal

from app import db, timefmt
from app.models import Inventory

EVENING = datetime(2026, 9, 29, 1, 30)   # 2026-09-28 21:30 in New York (EDT)


def test_utc_timestamp_shown_in_local_zone(monkeypatch):
    monkeypatch.setenv('TZ', 'America/New_York')
    assert timefmt.localtime_filter(EVENING) == '2026-09-28 21:30'
    assert timefmt.localtime_filter(EVENING, '%Y-%m-%d') == '2026-09-28'


def test_unset_or_unknown_zone_falls_back_to_utc(monkeypatch):
    monkeypatch.delenv('TZ', raising=False)
    assert timefmt.localtime_filter(EVENING) == '2026-09-29 01:30'
    monkeypatch.setenv('TZ', 'Not/AZone')
    assert timefmt.localtime_filter(EVENING) == '2026-09-29 01:30'


def test_missing_timestamp_uses_the_default():
    assert timefmt.localtime_filter(None) == '-'
    assert timefmt.localtime_filter(None, '%Y-%m-%d', '') == ''


def test_dashboard_added_date_is_local(client, make_spec, monkeypatch):
    monkeypatch.setenv('TZ', 'America/New_York')
    ram = make_spec('RAM', 'Samsung', 'M393B1G70QH0-YK0', ram_size=8, ram_modules=1)
    db.session.add(Inventory(component_type_id=ram.component_type_id, hardware_spec_id=ram.id,
                             quantity=2, status='Verified', created_at=EVENING))
    db.session.commit()
    page = client.get('/').get_data(as_text=True)
    assert '2026-09-28' in page and '2026-09-29' not in page


def test_blank_sale_date_uses_the_local_date(client, make_spec, monkeypatch):
    monkeypatch.setattr('app.routes.main.local_today', lambda: date(2026, 9, 28))
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    item = Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id,
                     quantity=1, status='Verified', purchase_price=Decimal('25'))
    db.session.add(item)
    db.session.commit()
    client.post(f'/inventory/{item.id}/sell', data={'quantity': '1', 'sale_price': '40'})
    db.session.expire_all()
    assert db.session.get(Inventory, item.id).sale_date == date(2026, 9, 28)
