"""
Backfill: run every existing storage_interface through
app.name_normalization.normalize_storage_interface(), the same function the
before_insert/before_update hook in app/models.py applies to new saves
(SATA generation phrasing, M.2/NVMe PCIe prefix cleanup).

Rows written before that hook existed (via the scraper, seed import, Open
WebUI/AI Import free text, manual spec edits, or backup restore) can still
hold variants like "SATA 6.0 Gbps" or "M.2 NVMe PCIe 4.0 x4". This brings
them in line with the canonical form ("SATA III", "PCIe 4.0 x4"). Note
"SATA 3.0 Gbps" normalizes to "SATA II", not "SATA III" -- it's a genuinely
slower spec (3.0 Gb/s vs 6.0 Gb/s), never merged into SATA III.

Dry run by default -- prints what would change and writes nothing.

    docker exec techreadout-app python -m app.maintenance.backfill_storage_interfaces
    docker exec techreadout-app python -m app.maintenance.backfill_storage_interfaces --apply
"""

import sys

from app import create_app, db
from app.models import HardwareSpec
from app.name_normalization import normalize_storage_interface


def main(apply: bool) -> None:
    app = create_app()
    with app.app_context():
        changes = []
        for spec in HardwareSpec.query.order_by(HardwareSpec.id).all():
            old = spec.storage_interface
            if not old:
                continue
            new = normalize_storage_interface(old)
            if new != old:
                changes.append((spec, old, new))

        if not changes:
            print("No storage_interface values need normalizing.")
            return

        for spec, old, new in changes:
            print(f"  id {spec.id:>4}  storage_interface  {old!r:<24} -> {new!r}"
                  f"   ({spec.manufacturer} {spec.model})")
        print(f"\n{len(changes)} value(s) to change.")

        if not apply:
            print("Dry run -- nothing written. Re-run with --apply to commit.")
            return

        for spec, _old, new in changes:
            spec.storage_interface = new
        db.session.commit()
        print("Committed.")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
