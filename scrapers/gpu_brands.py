"""GPU brand data — single source of truth (v3.8.10).

Before this module, "which strings count as an AIB board partner" and
"which suffixes are AIB variant marketing" each lived twice: once in
scoring.py (for confidence scoring) and once in normalization.py (for
building the TechPowerUp search query). The two lists had drifted apart
(normalization.py's was the larger one — it alone knew about kfa2,
leadtek, manli, maxsun, kuroutoshikou, and suffixes like windforce,
vapor-x, taichi). Both modules now import from here instead.

Three different things live in this module on purpose, because they get
used for three different jobs:

  GPU_CHIP_VENDORS   — NVIDIA / AMD / Intel. The only values that belong
                        in HardwareSpec.manufacturer for a GPU — it's a
                        shared reference spec, and "the chip" is the one
                        thing every board partner's card has in common.
  GPU_BOARD_PARTNERS — who builds the card (EVGA, MSI, ASUS, ...). This
                        is per-unit information, not per-spec, and belongs
                        on the Inventory row (custom_manufacturer), never
                        on the shared HardwareSpec.
  GPU_AIB_SUFFIXES   — marketing suffixes (Ventus, Strix, XC Gaming, ...)
                        stripped out so two listings of the *same card*
                        from different partners still match on core model.
"""

import re
from typing import Optional

GPU_CHIP_VENDORS = ('NVIDIA', 'AMD', 'Intel')

_GPU_CHIP_WORDS = {
    'nvidia': ('nvidia', 'geforce'),
    'amd': ('amd', 'radeon'),
    'intel': ('intel', 'arc'),
}

# Board partner (lowercase) -> chip maker(s) whose cards they build. Used
# both to credit a query's board brand as a match for the chip maker's
# reference spec (scoring.py) and to detect a board brand in a query/title
# at all (extract_board_partner, below).
#
# kfa2/kuroutoshikou/leadtek/manli are NVIDIA-only partners (mostly
# regional: EU/Japan/Asia); maxsun is primarily known for AMD cards.
# biostar makes the occasional GPU but is overwhelmingly a motherboard
# brand — included for name recognition/stripping, not scored as a chip
# match. Confirm/adjust these if any look wrong.
GPU_BOARD_PARTNERS = {
    'evga': {'nvidia'}, 'zotac': {'nvidia'}, 'pny': {'nvidia'}, 'palit': {'nvidia'},
    'gainward': {'nvidia'}, 'inno3d': {'nvidia'}, 'galax': {'nvidia'}, 'colorful': {'nvidia'},
    'kfa2': {'nvidia'}, 'kuroutoshikou': {'nvidia'}, 'leadtek': {'nvidia'}, 'manli': {'nvidia'},
    'sapphire': {'amd'}, 'xfx': {'amd'}, 'powercolor': {'amd'}, 'maxsun': {'amd'},
    'asrock': {'amd', 'intel'},
    'msi': {'nvidia', 'amd'}, 'asus': {'nvidia', 'amd'}, 'gigabyte': {'nvidia', 'amd'},
    'biostar': set(),
}

# Proper display casing for each board partner key above.
_BOARD_PARTNER_DISPLAY = {
    'evga': 'EVGA', 'zotac': 'ZOTAC', 'pny': 'PNY', 'palit': 'Palit',
    'gainward': 'Gainward', 'inno3d': 'Inno3D', 'galax': 'GALAX', 'colorful': 'Colorful',
    'kfa2': 'KFA2', 'kuroutoshikou': 'Kuroutoshikou', 'leadtek': 'Leadtek', 'manli': 'Manli',
    'sapphire': 'Sapphire', 'xfx': 'XFX', 'powercolor': 'PowerColor', 'maxsun': 'MaxSun',
    'asrock': 'ASRock', 'msi': 'MSI', 'asus': 'ASUS', 'gigabyte': 'Gigabyte', 'biostar': 'Biostar',
}

# Non-partner brand/edition words that still need stripping from a GPU query
# before search (e.g. "founders edition" isn't a board partner, NVIDIA sells
# those directly, but it's not part of the card's core model name either).
_NON_PARTNER_STRIP_TERMS = ('nvidia', 'amd', 'intel', 'founders edition')

# AIB marketing suffixes to strip when comparing/searching core model names.
# Union of the two lists that used to live separately in scoring.py and
# normalization.py.
GPU_AIB_SUFFIXES = frozenset({
    'xc', 'xc gaming', 'xc black', 'xc ultra', 'xc3',
    'sc', 'sc ultra', 'sc gaming',
    'ftw3', 'ftw3 ultra', 'ftw', 'ftw ultra', 'ftw2', 'ftw3 gaming',
    'strix', 'tuf gaming', 'tuf', 'rog strix', 'rog',
    'gaming x', 'gaming x trio', 'gaming trio', 'gaming oc', 'gaming z',
    'gaming z trio', 'black gaming', 'gaming',
    'ventus', 'ventus 2x', 'ventus 3x', 'suprim', 'suprim x', 'suprim liquid',
    'mech', 'mech oc', 'eagle', 'eagle oc',
    'vision', 'vision oc', 'aero', 'aero oc', 'aorus', 'aorus master', 'aorus elite',
    'armor', 'armor oc', 'duke', 'duke oc',
    'nitro+', 'nitro', 'pulse', 'pulse oc', 'toxic', 'vapor-x',
    'red devil', 'red dragon', 'fighter', 'fighter oc',
    'hellhound', 'speedster', 'challenger', 'phantom', 'phantom gaming',
    'black edition', 'overclocked', 'black', 'white',
    'trio', 'trio oc', 'trio gaming x',
    'dual', 'phoenix', 'proart', 'windforce',
    'amp extreme', 'amp holo', 'amp', 'twin edge',
    'xlr8', 'verto', 'uprising', 'epic-x',
    'gamerock', 'jetstream', 'sea hawk', 'taichi',
    'oc edition', 'oc', 'ultra', 'edition',
})

_GPU_BRAND_PREFIXES = frozenset({'nvidia', 'geforce', 'amd', 'radeon', 'intel', 'arc'})


def detect_gpu_chip_vendor(text: str) -> Optional[str]:
    """NVIDIA/AMD/Intel from a model name or page title, or None.

    Same rule used across every GPU source (TechPowerUp, seed data, etc.):
    this is the only thing that belongs in HardwareSpec.manufacturer for a
    GPU. Checks the explicit brand word first (NVIDIA/GeForce, AMD/Radeon,
    Intel), then falls back to the model-number family (GTX/RTX, RX, Arc)
    since plenty of real titles ("MSI GTX 1080 Aero 8G") never say the brand
    at all. Order doesn't matter beyond that — a real GPU title only ever
    names one chip maker.
    """
    upper = (text or '').upper()
    if 'NVIDIA' in upper or 'GEFORCE' in upper:
        return 'NVIDIA'
    if 'AMD' in upper or 'RADEON' in upper:
        return 'AMD'
    if 'INTEL' in upper or re.search(r'\bARC\b', upper):
        return 'Intel'
    if re.search(r'\b(GTX|RTX)\b', upper):
        return 'NVIDIA'
    if re.search(r'\bRX\b', upper):
        return 'AMD'
    return None


def extract_board_partner(text: str) -> Optional[str]:
    """The AIB board partner named in free text (a query or a product
    title), properly cased, or None if no known partner is present.

    Longest key first so e.g. a brand that's a substring of another known
    token still can't cause a false split (none currently collide, but this
    keeps future additions safe). Word-boundary matched so 'asus' doesn't
    match inside an unrelated longer word.
    """
    lowered = (text or '').lower()
    for partner in sorted(GPU_BOARD_PARTNERS, key=len, reverse=True):
        if re.search(r'\b' + re.escape(partner) + r'\b', lowered):
            return _BOARD_PARTNER_DISPLAY[partner]
    return None


def strip_aib_suffix(model: str) -> str:
    """Remove AIB variant words and GPU chip-brand prefixes from a model
    string, leaving only core model tokens for comparison (e.g. 'rtx 3060').
    """
    result = (model or '').lower()
    for prefix in _GPU_BRAND_PREFIXES:
        result = re.sub(r'\b' + prefix + r'\b', '', result)
    # Remove VRAM and clock specs — handled separately by callers.
    result = re.sub(r'\d+\s*gb', '', result, flags=re.I)
    result = re.sub(r'\d+\s*mhz', '', result, flags=re.I)
    for suffix in sorted(GPU_AIB_SUFFIXES, key=len, reverse=True):
        result = re.sub(r'\b' + re.escape(suffix) + r'\b', '', result)
    return re.sub(r'\s+', ' ', result).strip()


def gpu_query_strip_terms():
    """(partner_terms, suffix_terms) for normalize_gpu_query(): the full set
    of words to strip from a GPU search query, in the order normalize_gpu_query
    applies them (partners first, then suffixes).
    """
    partner_terms = list(GPU_BOARD_PARTNERS.keys()) + list(_NON_PARTNER_STRIP_TERMS)
    return partner_terms, list(GPU_AIB_SUFFIXES)
