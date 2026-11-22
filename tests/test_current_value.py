"""Current value tracking, separate from purchase price (v3.9.0).

Covers: eBay-apply writes current_value (not purchase_price); manual edit
can set current_value independently; host/compare value rollups prefer
current_value and fall back to purchase_price only when unset.
"""
from decimal import Decimal

from app import db
from app.models import AppSetting, Host, Inventory


def test_apply_ebay_price_sets_current_value_not_purchase_price(client, make_spec):
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    item = Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id, quantity=1,
                      item_condition='Used', status='Verified', purchase_price=Decimal('25.00'))
    db.session.add(item)
    db.session.commit()
    item_id = item.id

    resp = client.post(f'/inventory/{item_id}/apply-ebay-price', data={'price': '40.00'})
    assert resp.status_code == 302

    db.session.expire_all()
    item = db.session.get(Inventory, item_id)
    # Original cost basis is untouched
    assert item.purchase_price == Decimal('25.00')
    # Current value carries the eBay estimate
    assert item.current_value == Decimal('40.00')
    assert item.price_is_estimate is True
    assert item.current_value_updated_at is not None


def test_manual_edit_sets_current_value_and_clears_estimate_flag(client, make_spec):
    gpu = make_spec('GPU', 'NVIDIA', 'RTX 3060')
    item = Inventory(component_type_id=gpu.component_type_id, hardware_spec_id=gpu.id, quantity=1,
                      status='Verified', purchase_price=Decimal('200.00'),
                      current_value=Decimal('150.00'), price_is_estimate=True)
    db.session.add(item)
    db.session.commit()
    item_id = item.id

    resp = client.post(f'/inventory/{item_id}/edit', data={
        'component_type_id': gpu.component_type_id,
        'quantity': '1',
        'purchase_price': '200.00',
        'current_value': '175.00',
        'condition': 'Used',
        'status': 'Verified',
    })
    assert resp.status_code == 302

    db.session.expire_all()
    item = db.session.get(Inventory, item_id)
    assert item.current_value == Decimal('175.00')
    assert item.price_is_estimate is False  # manual entry clears the eBay flag


def test_fetch_ebay_price_divides_kit_price_by_ram_sticks(client, make_spec, monkeypatch):
    """A RAM listing's price is for the whole kit. An inventory row counts
    physical sticks (one row per stick, per the RAM-kit rule), so the price
    offered back to a single-stick row must be the kit price divided by the
    kit's stick count -- not the whole kit price applied to one stick.
    """
    monkeypatch.setenv('EBAY_APP_ID', 'x')
    monkeypatch.setenv('EBAY_APP_SECRET', 'x')
    AppSetting.set('ebay_pricing_enabled', 'true')
    db.session.commit()

    ram = make_spec('RAM', 'Kingston', 'Fury Beast 32GB DDR4-3600 CL18 KF436C18BB/32',
                     ram_size=32, ram_modules=2)
    item = Inventory(component_type_id=ram.component_type_id, hardware_spec_id=ram.id,
                      quantity=1, status='Verified')
    db.session.add(item)
    db.session.commit()
    item_id = item.id

    from app.scrapers import ebay as ebay_module
    monkeypatch.setattr(ebay_module, 'fetch_ebay_price',
                         lambda query: {'price': 199.00, 'listing_count': 12})

    resp = client.post(f'/inventory/{item_id}/ebay-price')
    assert resp.status_code == 200
    data = resp.get_json()
    # Kit estimate ($199) split across 2 sticks -> $99.50 for this one stick
    assert data['price'] == 99.50
    assert data['kit_price'] == 199.00
    assert data['per_stick_divisor'] == 2

    # A non-kit (single-module) RAM item isn't divided
    single = make_spec('RAM', 'Kingston', 'Fury Beast 16GB DDR4-3600 CL18 single',
                        ram_size=16, ram_modules=1)
    single_item = Inventory(component_type_id=single.component_type_id, hardware_spec_id=single.id,
                             quantity=1, status='Verified')
    db.session.add(single_item)
    db.session.commit()

    resp = client.post(f'/inventory/{single_item.id}/ebay-price')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['price'] == 199.00
    assert data['per_stick_divisor'] is None


def test_host_value_prefers_current_value_over_purchase_price(client, make_spec):
    host = Host(hostname='test-value-host', status='Active')
    db.session.add(host)
    db.session.commit()

    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    gpu = make_spec('GPU', 'NVIDIA', 'RTX 3060')

    # Priced via current_value (should use 40, not the stale 25 purchase price)
    priced_item = Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id, quantity=1,
                             status='Installed', purchase_price=Decimal('25.00'),
                             current_value=Decimal('40.00'), assigned_to_host_id=host.id)
    # Never had current_value fetched -- falls back to purchase_price
    unpriced_item = Inventory(component_type_id=gpu.component_type_id, hardware_spec_id=gpu.id, quantity=1,
                               status='Installed', purchase_price=Decimal('100.00'),
                               assigned_to_host_id=host.id)
    db.session.add_all([priced_item, unpriced_item])
    db.session.commit()

    resp = client.get(f'/hosts/{host.id}')
    assert resp.status_code == 200
    # 40.00 (current_value) + 100.00 (purchase_price fallback) = 140.00
    assert b'140.00' in resp.data
    # Make sure the stale purchase price (25.00) for the priced item isn't
    # the one driving the total
    assert b'65.00' not in resp.data
