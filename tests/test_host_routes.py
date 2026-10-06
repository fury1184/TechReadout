"""Host delete route (v3.8.12): blocked while components are assigned."""
from decimal import Decimal

from app import db
from app.models import Host, Inventory


def test_delete_host_removes_it(client):
    host = Host(hostname='test-empty-host', status='Active')
    db.session.add(host)
    db.session.commit()
    host_id = host.id

    resp = client.post(f'/hosts/{host_id}/delete')
    assert resp.status_code == 302

    db.session.expire_all()
    assert db.session.get(Host, host_id) is None


def test_delete_host_blocked_with_assigned_components(client, make_spec):
    host = Host(hostname='test-busy-host', status='Active')
    db.session.add(host)
    db.session.commit()

    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4')
    item = Inventory(component_type_id=cpu.component_type_id, hardware_spec_id=cpu.id, quantity=1,
                     item_condition='Used', status='Verified', purchase_price=Decimal('25.00'),
                     assigned_to_host_id=host.id)
    db.session.add(item)
    db.session.commit()
    host_id = host.id

    resp = client.post(f'/hosts/{host_id}/delete')
    assert resp.status_code == 302

    db.session.expire_all()
    assert db.session.get(Host, host_id) is not None
