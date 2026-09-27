"""Fixtures for route/query tests: the real app on an in-memory SQLite database.

create_app(config) skips its MariaDB-only CREATE TABLE statements on other
databases, and db.create_all() builds the schema from the models. SQLite
catches logic bugs, not MariaDB-specific behavior (collation, strict enums).
"""
import pytest

from app import create_app, db
from app.models import ComponentType, HardwareSpec

COMPONENT_TYPES = [
    'CPU', 'GPU', 'RAM', 'Motherboard', 'Storage', 'PSU',
    'Case', 'Cooler', 'Fan', 'NIC', 'Sound Card', 'Other',
]


@pytest.fixture
def flask_app():
    app = create_app({'SQLALCHEMY_DATABASE_URI': 'sqlite://', 'TESTING': True})
    with app.app_context():
        db.create_all()
        db.session.add_all(ComponentType(name=name) for name in COMPONENT_TYPES)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def make_spec(flask_app):
    """Create and return a HardwareSpec of the named component type."""
    def _make(type_name, manufacturer, model, **fields):
        ct = db.session.query(ComponentType).filter_by(name=type_name).one()
        spec = HardwareSpec(component_type_id=ct.id, manufacturer=manufacturer, model=model, **fields)
        db.session.add(spec)
        db.session.commit()
        return spec
    return _make
