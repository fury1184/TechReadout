"""migrations/init.sql must create every column the models map.

A fresh Docker volume gets its schema only from init.sql; the versioned
upgrade scripts never run there. v3.5.4 added ram_ecc/ram_module_type to the
model and an upgrade script but not to init.sql, so fresh installs failed on
every HardwareSpec query until v3.8.9.
"""
import re
from pathlib import Path

import pytest

from app import db
import app.models  # noqa: F401  (registers the tables on db.metadata)

INIT_SQL = Path(__file__).resolve().parent.parent / 'migrations' / 'init.sql'
_TABLE_CLAUSES = {'FOREIGN', 'INDEX', 'CONSTRAINT', 'PRIMARY', 'UNIQUE', 'KEY'}


def _init_sql_columns():
    sql = INIT_SQL.read_text(encoding='utf-8').replace('\r\n', '\n')
    tables = {}
    for match in re.finditer(r'CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);', sql, re.S):
        columns = set()
        for line in match.group(2).split('\n'):
            line = line.strip()
            if not line or line.startswith('--'):
                continue
            first = line.split()[0]
            if first.upper() in _TABLE_CLAUSES:  # table-level clause, not a column
                continue
            columns.add(first.strip('`,'))
        tables[match.group(1)] = columns
    return tables


MODEL_TABLES = sorted(db.metadata.tables.values(), key=lambda t: t.name)


@pytest.mark.parametrize('table', MODEL_TABLES, ids=lambda t: t.name)
def test_init_sql_has_every_model_column(table):
    tables = _init_sql_columns()
    assert table.name in tables, f"{table.name} has no CREATE TABLE in init.sql"
    missing = sorted({c.name for c in table.columns} - tables[table.name])
    assert not missing, f"init.sql {table.name} is missing: {missing}"
