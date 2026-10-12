"""Inventory Breakdown counts owned parts and RAM per stick (v3.8.9)."""
from app import db
from app.models import Inventory
from app.routes.stats import get_breakdown


def _add(spec, quantity, status):
    db.session.add(Inventory(component_type_id=spec.component_type_id, hardware_spec_id=spec.id,
                             quantity=quantity, status=status))
    db.session.commit()


def test_sold_and_dead_parts_are_not_counted(make_spec):
    cpu = make_spec('CPU', 'Intel', 'Xeon E5-2680 v4', cpu_socket='LGA 2011-3')
    _add(cpu, 1, 'Verified')
    _add(cpu, 1, 'Missing')   # still owned
    _add(cpu, 3, 'Sold')
    _add(cpu, 1, 'Dead')
    assert get_breakdown('CPU', 'cpu_socket') == [('LGA 2011-3', 2)]


def test_ram_is_grouped_by_stick_size(make_spec):
    kit = make_spec('RAM', 'Samsung', '32GB (2x16GB) DDR4-2400', ram_size=32, ram_modules=2)
    stick = make_spec('RAM', 'Crucial', '8GB DDR4-2666', ram_size=8, ram_modules=None)
    _add(kit, 2, 'Verified')     # one kit = two 16GB sticks
    _add(stick, 1, 'Verified')
    assert get_breakdown('RAM', 'ram_stick_size') == [(16, 2), (8, 1)]


def test_breakdown_page_renders(client, make_spec):
    kit = make_spec('RAM', 'Samsung', '32GB (2x16GB) DDR4-2400', ram_size=32, ram_modules=2, ram_type='DDR4')
    _add(kit, 2, 'Verified')
    page = client.get('/stats').get_data(as_text=True)
    assert 'Stick Size' in page
