"""Build planner: exact socket matching and plan quantities (v3.8.9)."""
import pytest

from app import db
from app.compatibility import check_build_plan
from app.models import BuildPlan, BuildPlanComponent, Inventory
from app.routes.planner import check_availability


def _stock(spec, quantity=1, status='Verified'):
    item = Inventory(component_type_id=spec.component_type_id, hardware_spec_id=spec.id,
                     quantity=quantity, status=status)
    db.session.add(item)
    db.session.commit()
    return item


def test_plan_socket_does_not_match_a_longer_socket_name(make_spec):
    _stock(make_spec('CPU', 'Intel', 'Core i7-3930K', cpu_socket='LGA 2011'))
    _stock(make_spec('CPU', 'Intel', 'Xeon E5-2680 v4', cpu_socket='LGA 2011-3'))
    _stock(make_spec('Motherboard', 'ASUS', 'P9X79', mobo_socket='LGA 2011'))
    _stock(make_spec('Motherboard', 'ASUS', 'X99-A', mobo_socket='LGA 2011-3'))
    plan = BuildPlan(name='X79 box', cpu_socket='LGA 2011')
    db.session.add(plan)
    db.session.commit()

    result = check_availability(plan)
    assert [i['name'] for i in result['cpu']['items']] == ['Intel Core i7-3930K']
    assert [i['name'] for i in result['motherboard']['items']] == ['ASUS P9X79']


@pytest.mark.parametrize('planned, expected_gb', [(2, 32.0), (20, 128.0)])
def test_plan_totals_use_the_plan_quantity(make_spec, planned, expected_gb):
    kit = make_spec('RAM', 'Samsung', '32GB (2x16GB) DDR4-2400', ram_size=32, ram_modules=2)
    row = _stock(kit, quantity=8)  # 8 sticks of 16GB on hand
    plan = BuildPlan(name='Test build')
    db.session.add(plan)
    db.session.flush()
    db.session.add(BuildPlanComponent(build_plan_id=plan.id, inventory_id=row.id,
                                      component_type_id=kit.component_type_id, quantity=planned))
    db.session.commit()

    # 2 planned -> 2 sticks; 20 planned -> capped at the 8 on hand
    assert check_build_plan(plan)['totals']['ram_gb'] == expected_gb


def test_plan_page_renders(client, make_spec):
    """/planner/<id> returned a 500 for every plan before v3.8.9: the template
    read availability.ram.items, which Jinja resolves to the dict's .items()."""
    kit = make_spec('RAM', 'Samsung', '32GB (2x16GB) DDR4-2400', ram_size=32, ram_modules=2)
    row = _stock(kit, quantity=8)
    plan = BuildPlan(name='Page test', min_ram_gb=32)
    empty = BuildPlan(name='Empty plan')
    db.session.add_all([plan, empty])
    db.session.flush()
    db.session.add(BuildPlanComponent(build_plan_id=plan.id, inventory_id=row.id,
                                      component_type_id=kit.component_type_id, quantity=2))
    db.session.commit()

    assert client.get(f'/planner/{empty.id}').status_code == 200
    page = client.get(f'/planner/{plan.id}')
    assert page.status_code == 200
    assert 'Samsung 32GB (2x16GB) DDR4-2400' in page.get_data(as_text=True)
