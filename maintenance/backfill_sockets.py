"""
Backfill: run every existing cpu_socket / mobo_socket through
app.name_normalization.canonical_spec_socket(), the same function the
before_insert/before_update hook in app/models.py applies to new saves
(spacing/dash normalization plus the derived LGA1151 "(300 Series)" marker).
Safe to re-run any time the rules change.

Rows written before that hook existed (or via raw SQL, which bypasses it)
can still hold variants like "LGA1155" or "LGA2011-V3". This brings them
in line with the canonical form ("LGA 1155", "LGA 2011-3").

Dry run by default -- prints what would change and writes nothing.

    docker exec techreadout-app python -m app.maintenance.backfill_sockets
    docker exec techreadout-app python -m app.maintenance.backfill_sockets --apply
"""

import sys

from app import create_app, db
from app.models import HardwareSpec
from app.name_normalization import canonical_spec_socket


def main(apply: bool) -> None:
    app = create_app()
    with app.app_context():
        changes = []
        for spec in HardwareSpec.query.order_by(HardwareSpec.id).all():
            for field in ("cpu_socket", "mobo_socket"):
                old = getattr(spec, field)
                if not old:
                    continue
                new = canonical_spec_socket(
                    field, old, model=spec.model, chipset=spec.mobo_chipset)
                if new != old:
                    changes.append((spec, field, old, new))

        if not changes:
            print("No socket values need normalizing.")
            return

        for spec, field, old, new in changes:
            print(f"  id {spec.id:>4}  {field:<11}  {old!r:<28} -> {new!r}"
                  f"   ({spec.manufacturer} {spec.model})")
        print(f"\n{len(changes)} value(s) to change.")

        if not apply:
            print("Dry run -- nothing written. Re-run with --apply to commit.")
            return

        for spec, field, _old, new in changes:
            setattr(spec, field, new)
        db.session.commit()
        print("Committed.")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
