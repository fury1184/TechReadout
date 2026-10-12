"""
Backfill: fix GPU HardwareSpec rows whose manufacturer was saved as an AIB
board brand (MSI, EVGA, ...) instead of the chip maker (NVIDIA/AMD/Intel).

Cause: parse_amazon_gpu() wrote whatever board brand it found in the Amazon
title straight into `manufacturer` (fixed in v3.8.10). HardwareSpec is a
shared reference spec, used by every brand's build of a given card, so a
board brand there shows up as a stray row in /stats "By Manufacturer" next
to NVIDIA/AMD/Intel instead of under the chip vendor it actually belongs to.

For each affected spec:
  - Re-derive the chip vendor from the model name.
  - If a correctly-labeled spec for the same card already exists, repoint
    every Inventory row linked to the broken spec over to it, and delete the
    broken duplicate -- but first, where an Inventory row's own
    custom_manufacturer is still blank, set it to the board brand being
    discarded, so that per-unit information isn't just thrown away.
  - Otherwise, just correct the manufacturer in place (no duplicate exists,
    nothing to merge).
  - A spec whose model name doesn't clearly say NVIDIA/AMD/Intel is skipped
    and listed for manual review rather than guessed at.

Dry run by default -- prints what would change and writes nothing.

    docker exec techreadout-app python -m app.maintenance.backfill_gpu_manufacturer
    docker exec techreadout-app python -m app.maintenance.backfill_gpu_manufacturer --apply
"""

import sys

from app import create_app, db
from app.models import ComponentType, HardwareSpec, Inventory
from app.duplicates import compact_duplicate_key
from app.routes.main import _clean_spec_references
from app.scrapers.gpu_brands import GPU_BOARD_PARTNERS, detect_gpu_chip_vendor


def _spec_key(manufacturer, model):
    return compact_duplicate_key(f"{manufacturer or ''} {model or ''}")


def main(apply: bool) -> None:
    app = create_app()
    with app.app_context():
        gpu_type = ComponentType.query.filter_by(name='GPU').first()
        if not gpu_type:
            print("No GPU component type found; nothing to do.")
            return

        gpu_specs = HardwareSpec.query.filter_by(component_type_id=gpu_type.id).all()

        # Every GPU spec indexed by a normalized (manufacturer + model) key,
        # so a correctly-saved duplicate of a broken spec's card can be found
        # if one already exists.
        by_key = {}
        for spec in gpu_specs:
            by_key.setdefault(_spec_key(spec.manufacturer, spec.model), []).append(spec)

        broken = [
            s for s in gpu_specs
            if (s.manufacturer or '').strip().lower() in GPU_BOARD_PARTNERS
        ]

        if not broken:
            print("No GPU specs have an AIB brand as their manufacturer. Nothing to do.")
            return

        renames = []    # (spec, old_mfr, new_mfr) -- fixed in place
        merges = []     # (spec, target, old_mfr, [Inventory, ...])
        skipped = []    # (spec, reason)

        for spec in broken:
            old_mfr = spec.manufacturer
            chip_vendor = detect_gpu_chip_vendor(spec.model)
            if not chip_vendor:
                skipped.append((spec, "can't tell NVIDIA/AMD/Intel from the model name"))
                continue

            target = None
            for candidate in by_key.get(_spec_key(chip_vendor, spec.model), []):
                if candidate.id != spec.id:
                    target = candidate
                    break

            if target:
                items = Inventory.query.filter_by(hardware_spec_id=spec.id).all()
                merges.append((spec, target, old_mfr, items))
            else:
                renames.append((spec, old_mfr, chip_vendor))

        if renames or merges:
            print(f"{len(renames)} spec(s) to fix in place, "
                  f"{len(merges)} duplicate(s) to merge into an existing correct spec:\n")

            for spec, old_mfr, new_mfr in renames:
                print(f"  RENAME  id {spec.id:>4}  {old_mfr!r} -> {new_mfr!r}"
                      f"   ({old_mfr} {spec.model})")

            for spec, target, old_mfr, items in merges:
                print(f"  MERGE   id {spec.id:>4} ({old_mfr} {spec.model}) "
                      f"-> id {target.id} ({target.manufacturer} {target.model})"
                      f"   [{len(items)} inventory row(s) to repoint]")
                for item in items:
                    note = (" -- custom_manufacturer already set, left alone"
                            if item.custom_manufacturer else
                            f" -- custom_manufacturer will be set to {old_mfr!r}")
                    print(f"      inventory id {item.id}: {item.display_name}{note}")
        else:
            print("Every affected spec was skipped (see below); nothing to fix automatically.")

        if skipped:
            print(f"\n{len(skipped)} spec(s) skipped (couldn't auto-fix -- check by hand):")
            for spec, reason in skipped:
                print(f"  id {spec.id:>4}  {spec.manufacturer!r} {spec.model!r} -- {reason}")

        if not renames and not merges:
            return

        if not apply:
            print("\nDry run -- nothing written. Re-run with --apply to commit.")
            return

        for spec, old_mfr, new_mfr in renames:
            spec.manufacturer = new_mfr

        for spec, target, old_mfr, items in merges:
            for item in items:
                if not item.custom_manufacturer:
                    item.custom_manufacturer = old_mfr
                item.hardware_spec_id = target.id
            _clean_spec_references(spec.id)
            db.session.delete(spec)

        db.session.commit()
        print("\nCommitted.")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
