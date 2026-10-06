"""Storage interface normalization (v3.8.12): SATA generation phrasing and
M.2/NVMe PCIe prefix cleanup, both at the HardwareSpec save hook and via
app.name_normalization.normalize_storage_interface directly.

SATA II (3.0 Gb/s) and SATA III (6.0 Gb/s) are different specs and must
never collapse into each other.
"""
import pytest

from app import db
from app.name_normalization import normalize_storage_interface


@pytest.mark.parametrize("raw,expected", [
    ("SATA III", "SATA III"),
    ("SATA III 6Gb/s", "SATA III"),
    ("SATA III 6 Gb/s", "SATA III"),
    ("SATA 6.0 Gbps", "SATA III"),
    ("SATA 6 Gbps", "SATA III"),
    ("sata iii", "SATA III"),
    ("SATA II", "SATA II"),
    ("SATA 3.0 Gbps", "SATA II"),
    ("SATA 3 Gbps", "SATA II"),
    ("SATA II 3Gb/s", "SATA II"),
    ("SATA I", "SATA I"),
    ("SATA 1.5 Gbps", "SATA I"),
    ("PCIe 4.0 x4", "PCIe 4.0 x4"),
    ("M.2 NVMe PCIe 4.0 x4", "PCIe 4.0 x4"),
    ("M.2 NVMe PCIe 3.0 x4", "PCIe 3.0 x4"),
    ("NVMe PCIe 5.0 x4", "PCIe 5.0 x4"),
    ("PCIe 4 x4", "PCIe 4.0 x4"),
    (None, None),
    ("", None),
    ("  ", None),
])
def test_normalize_storage_interface(raw, expected):
    assert normalize_storage_interface(raw) == expected


def test_sata_ii_never_collapses_into_sata_iii():
    # The whole point: these are different speeds, not formatting variants.
    assert normalize_storage_interface("SATA 3.0 Gbps") != normalize_storage_interface("SATA 6.0 Gbps")


def test_bare_sata_with_no_generation_is_left_unchanged():
    # No generation/speed given -- don't guess which one it is.
    assert normalize_storage_interface("SATA") == "SATA"


def test_unrecognized_speed_figure_is_left_unchanged():
    # Not a real SATA generation -- pass through rather than guess.
    assert normalize_storage_interface("SATA 12.0 Gbps") == "SATA 12.0 Gbps"


def test_save_hook_normalizes_storage_interface_on_insert(client, make_spec):
    spec = make_spec('Storage', 'Samsung', '870 EVO', storage_interface='SATA 6.0 Gbps')

    db.session.expire_all()
    assert spec.storage_interface == 'SATA III'


def test_save_hook_keeps_sata_ii_distinct_on_insert(client, make_spec):
    spec = make_spec('Storage', 'Seagate', 'Old 2.5in SSD', storage_interface='SATA 3.0 Gbps')

    db.session.expire_all()
    assert spec.storage_interface == 'SATA II'


def test_save_hook_normalizes_storage_interface_on_update(client, make_spec):
    spec = make_spec('Storage', 'WD', 'Blue')
    spec.storage_interface = 'M.2 NVMe PCIe 3.0 x4'
    db.session.commit()

    db.session.expire_all()
    assert spec.storage_interface == 'PCIe 3.0 x4'
