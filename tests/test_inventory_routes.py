"""Selling part of a row and adding through the JSON API (v3.8.9)."""
from decimal import Decimal

from app import db
from app.models import ComponentType, Inventory


def test_selling_part_of_a_row_splits_off_a_sold_row(client, make_spec):
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    item = Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id, quantity=2,
                     item_condition='Used', status='Verified', purchase_price=Decimal('25.00'))
    db.session.add(item)
    db.session.commit()

    resp = client.post(f'/inventory/{item.id}/sell',
                       data={'quantity': '1', 'sale_price': '40', 'sold_to': 'eBay'})
    assert resp.status_code == 302

    db.session.expire_all()
    rows = db.session.query(Inventory).order_by(Inventory.id).all()
    assert [(r.status, r.quantity, r.item_condition) for r in rows] == [
        ('Verified', 1, 'Used'),
        ('Sold', 1, 'Used'),
    ]
    assert rows[1].sale_price == Decimal('40.00')


def test_api_add_inventory_sets_condition(client):
    ct = db.session.query(ComponentType).filter_by(name='Other').one()
    resp = client.post('/api/inventory', json={
        'component_type_id': ct.id, 'custom_name': 'KVM switch', 'condition': 'Used',
    })
    assert resp.status_code == 201
    item = db.session.get(Inventory, resp.get_json()['id'])
    assert item.item_condition == 'Used'
