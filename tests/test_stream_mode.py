"""Stream Mode — hiding the dashboard's total inventory value for demos/streams, v3.8.15.

Toggled via AppSetting 'stream_mode' from the Lookup Settings page. Masks
only the dashboard's "Inventory Value" card; everything else (per-item
prices, build costs, etc.) renders normally regardless of the setting.
"""
from decimal import Decimal

from app import db
from app.display import MASK, moneymask
from app.models import AppSetting, Inventory


def _set_stream_mode(on):
    AppSetting.set('stream_mode', 'true' if on else 'false')
    db.session.commit()


def test_moneymask_passes_through_when_off(client):
    _set_stream_mode(False)
    assert moneymask('$12.34') == '$12.34'


def test_moneymask_masks_when_on(client):
    _set_stream_mode(True)
    assert moneymask('$12.34') == MASK


def test_dashboard_hides_inventory_value_when_on(client, make_spec):
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    db.session.add(Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id,
                             quantity=3, status='Verified', purchase_price=Decimal('273.00')))
    db.session.commit()
    # Inventory Value totals price*qty ($819), distinct from the per-item
    # price ($273) shown elsewhere on the same page, so we can tell them apart.

    page_off = client.get('/').get_data(as_text=True)
    assert '819' in page_off

    _set_stream_mode(True)
    page_on = client.get('/').get_data(as_text=True)
    assert '819' not in page_on
    assert '273' in page_on  # per-item price is unaffected
    assert MASK in page_on


def test_dashboard_per_item_price_still_shows_when_on(client, make_spec):
    """Only the total Inventory Value card is masked, not per-item prices."""
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    db.session.add(Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id,
                             quantity=1, status='Verified', purchase_price=Decimal('199.99')))
    db.session.commit()

    _set_stream_mode(True)
    page = client.get('/').get_data(as_text=True)
    assert '199.99' in page


def test_inventory_list_price_unaffected(client, make_spec):
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    db.session.add(Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id,
                             quantity=1, status='Verified', purchase_price=Decimal('199.99')))
    db.session.commit()

    _set_stream_mode(True)
    page = client.get('/inventory').get_data(as_text=True)
    assert '199.99' in page


def test_settings_page_round_trips_stream_mode_toggle(client):
    resp = client.post('/scraper/settings', data={'stream_mode': 'on'}, follow_redirects=True)
    assert resp.status_code == 200
    assert AppSetting.get_bool('stream_mode') is True

    client.post('/scraper/settings', data={}, follow_redirects=True)
    assert AppSetting.get_bool('stream_mode') is False


def test_stream_mode_shows_no_banner(client):
    _set_stream_mode(True)
    page_on = client.get('/').get_data(as_text=True)
    assert 'Stream Mode' not in page_on
