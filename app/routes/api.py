from flask import Blueprint, jsonify, request
from app import db
from app.models import ComponentType, HardwareSpec, Inventory, Host, AppSetting, LookupCache
from app.serializers.hardware import hardware_spec_to_dict
from app.inventory_rules import inventory_quantity

bp = Blueprint('api', __name__)


def _spec_to_dict(spec, source='database', confidence=100):
    """Serialize a HardwareSpec ORM object to the standard lookup response dict."""
    return hardware_spec_to_dict(spec, source=source, confidence_value=confidence)


def _save_scraper_result(result):
    """Save a scraper result dict to the DB and return the HardwareSpec object."""
    ct_name = result.get('component_type', 'Other')
    ct = db.session.query(ComponentType).filter_by(name=ct_name).first()
    if not ct:
        ct = ComponentType(name=ct_name)
        db.session.add(ct)
        db.session.flush()

    spec = HardwareSpec(
        component_type_id=ct.id,
        manufacturer=result.get('manufacturer'),
        model=result.get('model'),
        source_url=result.get('source_url'),
        raw_data=result.get('raw_data'),
        cpu_socket=result.get('cpu_socket'),
        cpu_cores=result.get('cpu_cores'),
        cpu_threads=result.get('cpu_threads'),
        cpu_base_clock=result.get('cpu_base_clock'),
        cpu_boost_clock=result.get('cpu_boost_clock'),
        cpu_tdp=result.get('cpu_tdp'),
        cpu_architecture=result.get('cpu_architecture'),
        gpu_memory_size=result.get('gpu_memory_size'),
        gpu_memory_type=result.get('gpu_memory_type'),
        gpu_base_clock=result.get('gpu_base_clock'),
        gpu_boost_clock=result.get('gpu_boost_clock'),
        gpu_tdp=result.get('gpu_tdp'),
        gpu_bus_interface=result.get('gpu_bus_interface'),
        mobo_socket=result.get('mobo_socket'),
        mobo_chipset=result.get('mobo_chipset'),
        mobo_form_factor=result.get('mobo_form_factor'),
        mobo_memory_slots=result.get('mobo_memory_slots'),
        mobo_memory_type=result.get('mobo_memory_type'),
        mobo_max_memory=result.get('mobo_max_memory'),
        mobo_pcie_x16_slots=result.get('mobo_pcie_x16_slots'),
        mobo_pcie_x4_slots=result.get('mobo_pcie_x4_slots'),
        mobo_pcie_x1_slots=result.get('mobo_pcie_x1_slots'),
        mobo_m2_slots=result.get('mobo_m2_slots'),
        mobo_sata_ports=result.get('mobo_sata_ports'),
        psu_wattage=result.get('psu_wattage'),
        psu_efficiency=result.get('psu_efficiency'),
        psu_modular=result.get('psu_modular'),
        psu_form_factor=result.get('psu_form_factor'),
        ram_size=result.get('ram_size'),
        ram_type=result.get('ram_type'),
        ram_speed=result.get('ram_speed'),
        ram_cas_latency=result.get('ram_cas_latency'),
        ram_modules=result.get('ram_modules'),
        storage_capacity=result.get('storage_capacity'),
        storage_type=result.get('storage_type'),
        storage_interface=result.get('storage_interface'),
        storage_read_speed=result.get('storage_read_speed'),
        storage_write_speed=result.get('storage_write_speed'),
        storage_form_factor=result.get('storage_form_factor'),
        cooler_type=result.get('cooler_type'),
        cooler_tdp_rating=result.get('cooler_tdp_rating'),
        cooler_height=result.get('cooler_height'),
        cooler_fan_size=result.get('cooler_fan_size'),
        cooler_socket_support=result.get('cooler_socket_support'),
        case_type=result.get('case_type'),
        case_form_factor=result.get('case_form_factor'),
        case_max_gpu_length=result.get('case_max_gpu_length'),
        case_max_cooler_height=result.get('case_max_cooler_height'),
        fan_size=result.get('fan_size'),
        fan_rpm_max=result.get('fan_rpm_max'),
        fan_airflow=result.get('fan_airflow'),
        fan_noise=result.get('fan_noise'),
        fan_connector=result.get('fan_connector'),
        nic_speed=result.get('nic_speed'),
        nic_interface=result.get('nic_interface'),
        nic_ports=result.get('nic_ports'),
        sound_interface=result.get('sound_interface'),
        sound_channels=result.get('sound_channels'),
        sound_sample_rate=result.get('sound_sample_rate'),
    )
    db.session.add(spec)
    db.session.commit()
    return spec


@bp.route('/lookup', methods=['POST'])
def lookup_hardware():
    """
    On-demand hardware lookup. Checks local DB first, then scrapes the web.
    Returns up to 3 scored candidates for human review when best confidence < 90%.
    Auto-accepts when confidence >= 90%.
    """
    import os, re
    from app.scrapers.lookup import lookup_hardware as do_lookup, score_candidate

    REVIEW_THRESHOLD = 90
    OPENWEBUI_CONFIDENCE_CAP = 89  # Open WebUI (LLM) results never auto-accept, however well they score

    data = request.get_json()
    query = (data.get('query') or '').strip()
    component_type = data.get('component_type', 'auto')

    if not query:
        return jsonify({'error': 'Query required'}), 400

    # ── Component-type filter ──────────────────────────────────────────────
    ct_filter = None
    if component_type and component_type != 'auto':
        ct = db.session.query(ComponentType).filter_by(name=component_type).first()
        if ct:
            ct_filter = ct.id

    def base_q():
        q = HardwareSpec.query
        if ct_filter:
            q = q.filter(HardwareSpec.component_type_id == ct_filter)
        return q

    # ── Normalisation helpers (unchanged from v1) ──────────────────────────
    def normalize_model(text):
        n = re.sub(r'\s*v\s*(\d+)', r'v\1', text, flags=re.I)
        return re.sub(r'\s*-\s*', '-', n).lower().strip()

    def extract_version(text):
        m = re.search(r'v(\d+)', text.lower())
        return m.group(1) if m else None

    def models_match(q_str, m_str):
        qn, mn = normalize_model(q_str), normalize_model(m_str)
        if qn == mn or qn in mn or mn in qn:
            qv, mv = extract_version(q_str), extract_version(m_str)
            if qv and mv and qv != mv:
                return False
            if bool(qv) != bool(mv):
                return False
            return True
        qv, mv = extract_version(q_str), extract_version(m_str)
        if bool(qv) != bool(mv):
            return False
        if qv and mv and qv != mv:
            return False
        qs = re.search(r'(\d{4,5})([kfxwsue]*)\b', normalize_model(q_str))
        ms_ = re.search(r'(\d{4,5})([kfxwsue]*)\b', normalize_model(m_str))
        if qs and ms_ and qs.group(1) == ms_.group(1) and qs.group(2) != ms_.group(2):
            return False
        return True

    query_norm = normalize_model(query)
    print(f"[DB Search] Query: '{query}', Type: {ct_filter}", flush=True)

    # ── Collect DB candidates (all strategies, deduped, scored) ───────────
    _seen_ids = {}   # spec_id → candidate dict

    known_mfg_keys = {
        'asus', 'msi', 'gigabyte', 'asrock', 'evga', 'nvidia', 'amd', 'intel',
        'corsair', 'samsung', 'zotac', 'pny', 'sapphire', 'xfx', 'powercolor',
        'palit', 'gainward', 'inno3d', 'galax', 'kingston', 'crucial', 'gskill',
        'g.skill', 'seasonic', 'noctua', 'cooler master', 'be quiet', 'biostar', 'colorful',
    }

    def add_db_candidate(spec):
        if not spec or spec.id in _seen_ids:
            return
        conf = score_candidate(query, spec.display_name, spec.manufacturer, component_type)
        d = _spec_to_dict(spec, source='database', confidence=conf)
        _seen_ids[spec.id] = d
        print(f"[DB Search] Candidate: {spec.display_name} → conf={conf}", flush=True)

    # Strategy 1: exact match
    for s in base_q().filter(HardwareSpec.model.ilike(query)).all():
        add_db_candidate(s)
    for s in base_q().filter(HardwareSpec.model.ilike(query_norm)).all():
        add_db_candidate(s)

    # Strategy 2: model contains query
    if len(query) >= 4:
        for s in base_q().filter(HardwareSpec.model.ilike(f'%{query}%')).all():
            if models_match(query, s.model):
                add_db_candidate(s)

    # Strategy 3: query contains model
    all_specs = base_q().all()
    for s in all_specs:
        if s.id in _seen_ids:
            continue
        if s.model and len(s.model) >= 4:
            if s.model.lower() in query.lower() and models_match(query, s.model):
                add_db_candidate(s)
            if s.manufacturer:
                full = f"{s.manufacturer} {s.model}".lower()
                if (full in query.lower() or query.lower() in full) and models_match(query, s.model):
                    add_db_candidate(s)

    # Strategy 4: fuzzy word overlap (recall boost — human review replaces hard gating)
    q_words = [w for w in query.lower().split() if len(w) >= 2]
    model_words = [w for w in q_words if w not in known_mfg_keys]
    if model_words:
        for s in all_specs:
            if s.id in _seen_ids:
                continue
            model_lower = (s.model or '').lower()
            matches = sum(1 for w in model_words if w in model_lower)
            if matches >= max(1, len(model_words) - 1):  # Allow 1 miss for recall
                add_db_candidate(s)

    sorted_db = sorted(_seen_ids.values(), key=lambda x: x['confidence'], reverse=True)
    best_db_conf = sorted_db[0]['confidence'] if sorted_db else 0
    print(f"[DB Search] {len(sorted_db)} candidates, best conf={best_db_conf}", flush=True)

    # ── Cache key ──────────────────────────────────────────────────────────
    cache_key = LookupCache.make_key(
        query, component_type,
        lite_mode=data.get('lite_mode', False),
        use_intel_ark=data.get('use_intel_ark', False),
        use_amd_official=data.get('use_amd_official', False),
    )

    # Auto-accept if best DB candidate clears threshold
    if best_db_conf >= REVIEW_THRESHOLD:
        best = sorted_db[0]
        LookupCache.store_hit(cache_key, query, component_type, best['spec_id'])
        db.session.commit()
        print(f"[DB Search] Auto-accept: {best['manufacturer']} {best['model']} conf={best_db_conf}", flush=True)
        return jsonify(best)

    # Check cache for a previously recorded miss (only skip scraper if no DB candidates either)
    cached = LookupCache.get_fresh(cache_key)
    if cached and cached.status == 'miss' and not sorted_db:
        return jsonify({'found': False, 'message': 'No specs found for that model. Try manual entry.'})

    # ── Web scraper ────────────────────────────────────────────────────────
    print(f"[Lookup] Falling through to web scraper for '{query}'", flush=True)
    result = do_lookup(
        query, component_type,
        lite_mode=data.get('lite_mode', False),
        use_intel_ark=data.get('use_intel_ark', False),
        use_amd_official=data.get('use_amd_official', False),
    )

    scraper_candidate = None

    if result and not result.get('error'):
        # Save to DB immediately (same as before — it's a reference spec regardless)
        saved_spec = _save_scraper_result(result)
        source = result.get('source', 'scraped')
        # Prefer scraper-provided confidence because it includes source trust and
        # spec completeness. Fall back to name-match confidence for older results.
        conf = result.get('confidence')
        if conf is None:
            conf = score_candidate(
                query,
                saved_spec.display_name,
                saved_spec.manufacturer,
                component_type,
            )
        conf = int(conf)
        if source == 'openwebui':
            # LLM guesses always need a human sanity-check, no matter how
            # well the name matches — cap below REVIEW_THRESHOLD so this
            # can never auto-accept, while still preserving the real score
            # as a useful triage signal in the review queue.
            conf = min(conf, OPENWEBUI_CONFIDENCE_CAP)
        scraper_candidate = _spec_to_dict(saved_spec, source=source, confidence=conf)
        print(f"[Lookup] Scraper result: {saved_spec.display_name} conf={conf}", flush=True)

        # Auto-accept if scraper result clears threshold
        if conf >= REVIEW_THRESHOLD:
            LookupCache.store_hit(cache_key, query, component_type, saved_spec.id)
            db.session.commit()
            return jsonify(scraper_candidate)

    elif result:
        err = result.get('error')
        if err == 'unsupported_type':
            return jsonify({
                'found': False,
                'message': f"Spec lookup only works for CPU, GPU, Motherboard, and PSU. "
                           f"Add {result.get('component_type', 'this item')} as a custom entry.",
                'unsupported_type': True,
            })
        if err == 'scrapedo_budget_exhausted':
            return jsonify({'found': False, 'message': 'Paid lookup budget reached for this request.',
                            'scrapedo_budget_exhausted': True})
        if err == 'credits_exhausted':
            return jsonify({'found': False, 'message': 'Scrape.Do API credits exhausted.',
                            'credits_exhausted': True})

    # ── Merge all candidates and decide ────────────────────────────────────
    all_candidates = list(sorted_db[:3])
    if scraper_candidate:
        all_candidates.append(scraper_candidate)
    all_candidates.sort(key=lambda x: x['confidence'], reverse=True)
    all_candidates = all_candidates[:3]

    if not all_candidates:
        LookupCache.store_miss(cache_key, query, component_type)
        db.session.commit()
        return jsonify({'found': False, 'message': 'No specs found for that model. Try manual entry.'})

    best_conf = all_candidates[0]['confidence']

    if best_conf >= REVIEW_THRESHOLD:
        best = all_candidates[0]
        LookupCache.store_hit(cache_key, query, component_type, best['spec_id'])
        db.session.commit()
        print(f"[Lookup] Auto-accept (merged): conf={best_conf}", flush=True)
        return jsonify(best)

    # Below threshold — return candidates for human review
    print(f"[Lookup] Needs review: best_conf={best_conf} < {REVIEW_THRESHOLD}", flush=True)
    return jsonify({
        'status': 'needs_review',
        'found': False,
        'candidates': all_candidates,
        'query': query,
        'component_type': component_type,
    })


@bp.route('/confirm-lookup', methods=['POST'])
def confirm_lookup():
    """
    Accept a human-selected candidate from the review modal.
    Looks up the spec by ID, caches it, and returns the full spec response.
    """
    from app.scrapers.lookup import score_candidate

    data = request.get_json()
    spec_id = data.get('spec_id')
    query = (data.get('query') or '').strip()
    component_type = (data.get('component_type') or 'auto').strip()

    if not spec_id:
        return jsonify({'error': 'spec_id required'}), 400

    spec = db.session.get(HardwareSpec, spec_id)
    if not spec:
        return jsonify({'error': 'Spec not found'}), 404

    # Cache as a hit so future lookups for the same query skip the scraper
    if query:
        cache_key = LookupCache.make_key(query, component_type)
        LookupCache.store_hit(cache_key, query, component_type, spec.id)
        db.session.commit()

    conf = score_candidate(query, spec.display_name, spec.manufacturer, component_type)
    result = _spec_to_dict(spec, source='human_review', confidence=conf)
    print(f"[Confirm] Human selected: {spec.display_name} conf={conf}", flush=True)
    return jsonify(result)
    

@bp.route('/specs')
def get_specs():
    """Get hardware specs with optional filtering."""
    component_type = request.args.get('type')
    manufacturer = request.args.get('manufacturer')
    search = request.args.get('q')
    limit = request.args.get('limit', 50, type=int)
    
    query = HardwareSpec.query
    
    if component_type:
        ct = db.session.query(ComponentType).filter_by(name=component_type).first()
        if ct:
            query = query.filter_by(component_type_id=ct.id)
    
    if manufacturer:
        query = query.filter_by(manufacturer=manufacturer)
    
    if search:
        query = query.filter(
            db.or_(
                HardwareSpec.model.ilike(f'%{search}%'),
                HardwareSpec.manufacturer.ilike(f'%{search}%')
            )
        )
    
    specs = query.limit(limit).all()
    
    return jsonify([{
        'id': s.id,
        'component_type': s.component_type.name,
        'manufacturer': s.manufacturer,
        'model': s.model,
        'display_name': s.display_name
    } for s in specs])


@bp.route('/specs/<int:id>')
def get_spec_detail(id):
    """Get detailed spec info."""
    spec = db.session.get_or_404(HardwareSpec, id)
    
    data = {
        'id': spec.id,
        'component_type': spec.component_type.name,
        'manufacturer': spec.manufacturer,
        'model': spec.model,
        'display_name': spec.display_name,
        'release_date': spec.release_date.isoformat() if spec.release_date else None,
        'msrp': float(spec.msrp) if spec.msrp else None,
        'source_url': spec.source_url
    }
    
    # Add type-specific fields
    if spec.component_type.name == 'CPU':
        data.update({
            'socket': spec.cpu_socket,
            'cores': spec.cpu_cores,
            'threads': spec.cpu_threads,
            'base_clock': float(spec.cpu_base_clock) if spec.cpu_base_clock else None,
            'boost_clock': float(spec.cpu_boost_clock) if spec.cpu_boost_clock else None,
            'tdp': spec.cpu_tdp,
            'architecture': spec.cpu_architecture
        })
    elif spec.component_type.name == 'GPU':
        data.update({
            'memory_size': spec.gpu_memory_size,
            'memory_type': spec.gpu_memory_type,
            'base_clock': spec.gpu_base_clock,
            'boost_clock': spec.gpu_boost_clock,
            'tdp': spec.gpu_tdp,
            'bus_interface': spec.gpu_bus_interface
        })
    elif spec.component_type.name == 'RAM':
        per_stick_size = None
        try:
            if spec.ram_size and spec.ram_modules:
                per_stick_size = spec.ram_size / spec.ram_modules
        except (TypeError, ZeroDivisionError):
            per_stick_size = None
        data.update({
            'size': spec.ram_size,
            'type': spec.ram_type,
            'speed': spec.ram_speed,
            'cas_latency': spec.ram_cas_latency,
            'modules': spec.ram_modules,
            'per_stick_size': per_stick_size,
        })
    
    return jsonify(data)


@bp.route('/inventory')
def get_inventory():
    """Get inventory items."""
    items = db.session.query(Inventory).all()
    
    return jsonify([{
        'id': i.id,
        'display_name': i.display_name,
        'component_type': i.component_type.name,
        'quantity': i.quantity,
        'status': i.status,
        'location': i.location,
        'condition': i.item_condition
    } for i in items])


@bp.route('/inventory', methods=['POST'])
def add_inventory():
    """Add inventory item via API."""
    data = request.get_json()
    spec = HardwareSpec.query.get(data.get('hardware_spec_id')) if data.get('hardware_spec_id') else None
    ct = ComponentType.query.get(data['component_type_id'])
    qty = inventory_quantity(data.get('quantity', 1), ct.name if ct else None, spec=spec, data=data)
    
    item = Inventory(
        hardware_spec_id=data.get('hardware_spec_id'),
        component_type_id=data['component_type_id'],
        custom_name=data.get('custom_name'),
        custom_manufacturer=data.get('custom_manufacturer'),
        quantity=qty,
        purchase_price=data.get('purchase_price'),
        condition=data.get('condition', 'New'),
        location=data.get('location'),
        notes=data.get('notes'),
        status=data.get('status', 'Unverified')
    )
    
    db.session.add(item)
    db.session.commit()
    
    return jsonify({'id': item.id, 'message': 'Item added'}), 201


@bp.route('/hosts')
def get_hosts():
    """Get all hosts."""
    hosts = db.session.query(Host).all()
    
    return jsonify([{
        'id': h.id,
        'hostname': h.hostname,
        'purpose': h.purpose,
        'os': h.os,
        'status': h.status,
        'component_count': h.components.count()
    } for h in hosts])


@bp.route('/component-types')
def get_component_types():
    """Get all component types."""
    types = db.session.query(ComponentType).all()
    return jsonify([{'id': t.id, 'name': t.name} for t in types])


@bp.route('/stats')
def get_stats():
    """Get dashboard statistics."""
    return jsonify({
        'total_specs': db.session.query(HardwareSpec).count(),
        'total_inventory_items': db.session.query(Inventory).count(),
        'total_quantity': db.session.query(db.func.sum(Inventory.quantity)).scalar() or 0,
        'total_hosts': db.session.query(Host).count(),
        'available_items': db.session.query(Inventory).filter(
            Inventory.status.in_(['Verified', 'Unverified'])
        ).count(),
        'in_use_items': db.session.query(Inventory).filter_by(status='Installed').count()
    })


@bp.route('/credits')
def get_credits():
    """Check Scrape.Do API credit balance."""
    import os
    import requests
    
    token = os.environ.get('SCRAPEDO_TOKEN')
    scrapedo_enabled = AppSetting.get_bool('enable_scrapedo_fallback', True)

    if not token:
        return jsonify({'error': 'Scrape.Do token not configured'})
    if not scrapedo_enabled:
        return jsonify({'error': 'Scrape.Do fallback is disabled in settings'})
    
    try:
        # Scrape.Do returns credit info in response headers
        # Make a minimal request to check
        response = requests.get(
            f'https://api.scrape.do?token={token}&url=https://example.com',
            timeout=30
        )
        
        # Check headers for credit info
        remaining = response.headers.get('X-Credits-Remaining') or response.headers.get('x-credits-remaining')
        used = response.headers.get('X-Credits-Used') or response.headers.get('x-credits-used')
        
        if remaining:
            return jsonify({
                'remaining': int(remaining),
                'used_this_request': int(used) if used else 1,
                'reset_date': 'Monthly'
            })
        
        # If no headers, try to parse from response or estimate
        if response.status_code == 200:
            return jsonify({
                'status': 'active',
                'message': 'API is working but credit info not available in headers'
            })
        elif response.status_code in [402, 403]:
            return jsonify({
                'remaining': 0,
                'error': 'Credits exhausted'
            })
        else:
            return jsonify({
                'error': f'API returned status {response.status_code}'
            })
            
    except Exception as e:
        return jsonify({'error': str(e)})
