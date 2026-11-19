"""Current value tracking, separate from purchase price (v3.9.0).

Covers: eBay-apply writes current_value (not purchase_price); manual edit
can set current_value independently; host/compare value rollups prefer
current_value and fall back to purchase_price only when unset.
"""
from decimal import Decimal

from app import db
from app.models import Host, Inventory


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
