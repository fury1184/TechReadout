"""Shared inventory quantity rules for TechReadOut."""


def positive_int(value):
    """Return a positive integer from form/JSON-like values, otherwise None."""
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def ram_module_count(component_type_name, spec=None, data=None):
    """
    Return the RAM module/stick count for a RAM kit when known.

    TechReadOut stores RAM specs as the kit description, e.g. 32GB (2x16GB),
    but inventory quantity represents physical items. For RAM, physical items
    are modules/sticks.
    """
    if component_type_name != 'RAM':
        return None

    modules = None
    if spec is not None:
        modules = positive_int(getattr(spec, 'ram_modules', None))
    if modules is None and data is not None:
        getter = data.get if hasattr(data, 'get') else lambda key, default=None: default
        modules = positive_int(getter('ram_modules'))
    return modules if modules and modules > 1 else None


def inventory_quantity(entered_quantity=1, component_type_name=None, spec=None, data=None):
    """
    Calculate inventory quantity with the RAM kit -> physical stick rule.

    If the user leaves quantity at the default 1 for a RAM kit, one kit becomes
    N inventory units where N is ram_modules. If the user manually enters a
    higher quantity, respect that value because it may represent multiple kits
    or loose sticks.
    """
    qty = positive_int(entered_quantity) or 1
    modules = ram_module_count(component_type_name, spec=spec, data=data)
    if modules and qty == 1:
        return modules
    return qty
