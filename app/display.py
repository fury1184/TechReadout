"""Stream Mode — mask the dashboard's total inventory value for demos/streams.

Purely a presentation toggle: when enabled (AppSetting 'stream_mode'), the
`moneymask` Jinja filter renders a placeholder instead of the real number
wherever it's applied — currently just the dashboard's "Inventory Value"
card. Nothing is deleted or recalculated differently; this only changes what
gets drawn on screen, so it's safe to flip on mid-demo and back off after.
"""
from app.models import AppSetting

MASK = '•••••'


def is_stream_mode():
    """Whether Stream Mode is currently on. Not cached: AppSetting.get is a
    single primary-key lookup, and other toggles in the app aren't cached
    either, so this stays simple and always reflects the latest save.
    """
    return AppSetting.get_bool('stream_mode', False)


def moneymask(value):
    """Jinja filter: {{ ('$' ~ '%.2f'|format(item.purchase_price)) | moneymask }}

    Pass it the already-formatted display string (or number) for a dollar
    value; it's returned unchanged unless Stream Mode is on, in which case
    a fixed-width placeholder is returned instead.
    """
    return MASK if is_stream_mode() else value
