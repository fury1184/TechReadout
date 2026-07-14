"""Scraper confidence scoring helpers for TechReadOut.

This module keeps candidate scoring separate from the lookup orchestration.
Unknown values should stay ``None``/``null`` and are scored as incomplete rather
than guessed.
"""

import re
from datetime import datetime, timezone
from typing import Optional, Dict

from app.scrapers.validation import has_minimum_specs, present_spec_fields

AIB_SUFFIXES = frozenset({
    'xc', 'xc gaming', 'xc black', 'xc ultra', 'sc', 'sc ultra', 'sc gaming',
    'ftw3', 'ftw3 ultra', 'ftw', 'ftw ultra', 'ftw2', 'ftw3 gaming',
    'strix', 'tuf gaming', 'tuf', 'rog strix', 'rog',
    'gaming x', 'gaming x trio', 'gaming trio', 'gaming oc', 'gaming z',
    'ventus', 'ventus 2x', 'ventus 3x', 'suprim', 'suprim x', 'suprim liquid',
    'mech', 'mech oc', 'eagle', 'eagle oc',
    'vision', 'vision oc', 'aero', 'aero oc', 'aorus', 'aorus master', 'aorus elite',
    'armor', 'armor oc', 'duke', 'duke oc',
    'nitro+', 'nitro', 'pulse', 'pulse oc',
    'red devil', 'red dragon', 'fighter', 'fighter oc',
    'hellhound', 'speedster', 'challenger', 'phantom',
    'black edition', 'overclocked',
    'trio', 'trio oc', 'trio gaming x',
})

_SCORE_MANUFACTURERS = frozenset({
    'asus', 'msi', 'gigabyte', 'asrock', 'evga', 'nvidia', 'amd', 'intel',
    'corsair', 'samsung', 'zotac', 'pny', 'sapphire', 'xfx', 'powercolor',
    'palit', 'gainward', 'inno3d', 'galax', 'kingston', 'crucial',
    'g.skill', 'gskill', 'seasonic', 'noctua', 'cooler master', 'be quiet',
    'biostar', 'colorful',
})

_GPU_BRAND_PREFIXES = frozenset({'nvidia', 'geforce', 'amd', 'radeon', 'intel', 'arc'})

_VRAM_RE = re.compile(r'(\d+)\s*gb', re.I)

def extract_vram_gb(text: str) -> Optional[int]:
    """Extract a GB value from text (VRAM, RAM capacity, etc.)."""
    m = _VRAM_RE.search(text or '')
    return int(m.group(1)) if m else None


def strip_aib_suffix(model: str) -> str:
    """
    Remove AIB variant words and GPU branding prefixes from a model string,
    leaving only the core model tokens for comparison (e.g. 'rtx 3060').
    """
    result = (model or '').lower()
    for prefix in _GPU_BRAND_PREFIXES:
        result = re.sub(r'\b' + prefix + r'\b', '', result)
    # Remove VRAM and clock specs — handled separately
    result = re.sub(r'\d+\s*gb', '', result, flags=re.I)
    result = re.sub(r'\d+\s*mhz', '', result, flags=re.I)
    # Remove AIB suffixes longest-first to avoid partial matches
    for suffix in sorted(AIB_SUFFIXES, key=len, reverse=True):
        result = re.sub(r'\b' + re.escape(suffix) + r'\b', '', result)
    return re.sub(r'\s+', ' ', result).strip()


def score_candidate(query: str, candidate_name: str, candidate_manufacturer: str,
                    component_type: str = 'GPU') -> int:
    """
    Score a scrape or DB candidate against the original search query.
    Returns an integer 0–100 representing match confidence.

    Weights (sum = 100):
      Manufacturer : 25 pts  — neutral (full pts) when mfr not in query
      Core model   : 40 pts  — token overlap after stripping AIB suffixes
      VRAM / RAM   : 20 pts  — neutral when not specified in query;
                               partial (8 pts) when candidate omits it
      AIB variant  : 15 pts  — neutral when not specified in query
    """
    query_lower = (query or '').lower()
    cand_lower = (candidate_name or '').lower()
    cand_mfg_lower = (candidate_manufacturer or '').lower()
    score = 0

    # ── Manufacturer (25 pts) ──────────────────────────────────────────────
    query_mfg = None
    for mfg in _SCORE_MANUFACTURERS:
        if re.search(r'\b' + re.escape(mfg) + r'\b', query_lower):
            query_mfg = mfg
            break

    if query_mfg:
        if query_mfg in cand_mfg_lower or query_mfg in cand_lower:
            score += 25
        # Mismatch → 0 pts (not blocked; human review can still see it)
    else:
        score += 25  # No mfr in query → neutral → full pts

    # ── VRAM / Memory (20 pts) ────────────────────────────────────────────
    use_mem = component_type in ('GPU', 'RAM')
    query_vram = extract_vram_gb(query) if use_mem else None

    if query_vram:
        cand_vram = extract_vram_gb(candidate_name)
        if cand_vram == query_vram:
            score += 20
        elif cand_vram is None:
            score += 8   # Candidate title doesn't list VRAM → partial credit
        # Wrong VRAM → 0 pts
    else:
        score += 20  # No VRAM in query → neutral → full pts

    # ── Core model (40 pts) ───────────────────────────────────────────────
    q_core = strip_aib_suffix(query)
    c_core = strip_aib_suffix(candidate_name)
    q_tokens = {t for t in re.split(r'[\s\-]+', q_core) if len(t) >= 2}
    c_tokens = {t for t in re.split(r'[\s\-]+', c_core) if len(t) >= 2}

    if q_tokens:
        overlap = len(q_tokens & c_tokens) / len(q_tokens)
        score += int(40 * overlap)

    # ── AIB variant (15 pts) ──────────────────────────────────────────────
    q_variants = {s for s in AIB_SUFFIXES
                  if re.search(r'\b' + re.escape(s) + r'\b', query_lower)}
    if q_variants:
        c_variants = {s for s in AIB_SUFFIXES
                      if re.search(r'\b' + re.escape(s) + r'\b', cand_lower)}
        score += 15 if (q_variants & c_variants) else 0
    else:
        score += 15  # No variant in query → neutral → full pts

    return min(score, 100)


SOURCE_TRUST = {
    'intel_ark': 98,
    'amd_official': 96,
    'manufacturer': 95,
    'techpowerup': 93,
    'amazon': 74,
    'scraped': 70,
    'openwebui': 65,
}


def source_display_name(source: str) -> str:
    names = {
        'intel_ark': 'Intel ARK',
        'amd_official': 'AMD Official',
        'manufacturer': 'Manufacturer',
        'techpowerup': 'TechPowerUp',
        'amazon': 'Amazon',
        'openwebui': 'Open WebUI',
    }
    return names.get(source or '', source or 'Unknown')


def spec_completeness_score(result: dict, component_type: str) -> int:
    """Return a 0-100 score for how complete the scraped spec payload is."""
    required_counts = {
        'CPU': 6,
        'GPU': 5,
        'RAM': 5,
        'Storage': 4,
        'Motherboard': 8,
        'PSU': 4,
        'Cooler': 4,
        'Case': 3,
        'Fan': 3,
    }
    present = len(present_spec_fields(result, component_type))
    target = required_counts.get(component_type, 2)
    if target <= 0:
        return 100
    return min(100, int((present / target) * 100))


def enrich_scrape_result(query: str, result: dict, component_type: str) -> Optional[Dict]:
    """Attach source, matching, and completeness metadata to a scrape result."""
    if not result:
        return None

    result = dict(result)
    result.setdefault('component_type', component_type)
    source = result.get('source') or 'scraped'
    source_trust = SOURCE_TRUST.get(source, SOURCE_TRUST['scraped'])
    match_score = score_candidate(
        query,
        result.get('model') or '',
        result.get('manufacturer') or '',
        component_type,
    )
    completeness = spec_completeness_score(result, component_type)

    confidence = int(round((match_score * 0.55) + (source_trust * 0.30) + (completeness * 0.15)))
    if not has_minimum_specs(result, component_type):
        confidence = min(confidence, 69)
    if source == 'openwebui':
        confidence = min(confidence, 89)

    metadata = {
        'source_name': source_display_name(source),
        'source_trust': source_trust,
        'match_score': match_score,
        'spec_completeness': completeness,
        'confidence': confidence,
        'scraped_at': datetime.now(timezone.utc).isoformat(),
    }

    raw_data = result.get('raw_data')
    if not isinstance(raw_data, dict):
        raw_data = {}
    raw_data['_lookup_metadata'] = metadata
    result['raw_data'] = raw_data
    result['source_name'] = metadata['source_name']
    result['confidence'] = confidence
    result['scraped_at'] = metadata['scraped_at']
    return result

# Backwards-compatible private aliases used by older code/tests.
_SOURCE_TRUST = SOURCE_TRUST
_source_display_name = source_display_name
_spec_completeness_score = spec_completeness_score
