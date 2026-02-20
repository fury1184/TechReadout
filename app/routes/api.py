from flask import Blueprint, jsonify, request
from app import db
from app.models import ComponentType, HardwareSpec, Inventory, Host

bp = Blueprint('api', __name__)


@bp.route('/lookup', methods=['POST'])
def lookup_hardware():
    """
    On-demand hardware lookup. Searches TechPowerUp for specs.
    If found, saves to hardware_specs and returns the data.
    """
    import os
    from app.scrapers.lookup import lookup_hardware as do_lookup
    
    data = request.get_json()
    query = data.get('query', '').strip()
    component_type = data.get('component_type', 'auto')
    
    if not query:
        return jsonify({'error': 'Query required'}), 400
    
    # Check if already in database
    existing = HardwareSpec.query.filter(
        db.or_(
            HardwareSpec.model.ilike(f'%{query}%'),
            db.and_(
                HardwareSpec.manufacturer.isnot(None),
                db.func.concat(HardwareSpec.manufacturer, ' ', HardwareSpec.model).ilike(f'%{query}%')
            )
        )
    ).first()
    
    if existing:
        return jsonify({
            'found': True,
            'source': 'database',
            'spec_id': existing.id,
            'manufacturer': existing.manufacturer,
            'model': existing.model,
            'component_type': existing.component_type.name,
            # CPU fields
            'cpu_socket': existing.cpu_socket,
            'cpu_cores': existing.cpu_cores,
            'cpu_threads': existing.cpu_threads,
            'cpu_base_clock': float(existing.cpu_base_clock) if existing.cpu_base_clock else None,
            'cpu_boost_clock': float(existing.cpu_boost_clock) if existing.cpu_boost_clock else None,
            'cpu_tdp': existing.cpu_tdp,
            # GPU fields
            'gpu_memory_size': existing.gpu_memory_size,
            'gpu_memory_type': existing.gpu_memory_type,
            'gpu_base_clock': existing.gpu_base_clock,
            'gpu_boost_clock': existing.gpu_boost_clock,
            'gpu_tdp': existing.gpu_tdp,
            # Motherboard fields
            'mobo_socket': existing.mobo_socket,
            'mobo_chipset': existing.mobo_chipset,
            'mobo_form_factor': existing.mobo_form_factor,
            'mobo_memory_slots': existing.mobo_memory_slots,
            'mobo_memory_type': existing.mobo_memory_type,
            'mobo_max_memory': existing.mobo_max_memory,
            'mobo_pcie_x16_slots': existing.mobo_pcie_x16_slots,
            # PSU fields
            'psu_wattage': existing.psu_wattage,
            'psu_efficiency': existing.psu_efficiency,
            'psu_modular': existing.psu_modular,
            'psu_form_factor': existing.psu_form_factor,
        })
    
    # Check if SCRAPEDO_TOKEN is configured
    if not os.environ.get('SCRAPEDO_TOKEN'):
        return jsonify({
            'found': False, 
            'message': 'Spec lookup requires a Scrape.Do API token. See README for setup instructions.',
            'no_token': True
        })
    
    # Scrape from web
    result = do_lookup(query, component_type)
    
    if not result:
        return jsonify({'found': False, 'message': 'No specs found for that model'})
    
    # Check for unsupported component type
    if result.get('error') == 'unsupported_type':
        return jsonify({
            'found': False,
            'message': f"Spec lookup only works for CPU, GPU, Motherboard, and PSU. Add {result.get('component_type', 'this item')} as a custom entry.",
            'unsupported_type': True
        })
    
    # Check for credits exhausted error
    if result.get('error') == 'credits_exhausted':
        return jsonify({
            'found': False,
            'message': 'Scrape.Do API credits exhausted. Credits reset monthly. Visit scrape.do to check your usage or upgrade.',
            'credits_exhausted': True
        })
    
    # Save to database
    ct_name = result.get('component_type', 'Other')
    ct = ComponentType.query.filter_by(name=ct_name).first()
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
        # CPU fields
        cpu_socket=result.get('cpu_socket'),
        cpu_cores=result.get('cpu_cores'),
        cpu_threads=result.get('cpu_threads'),
        cpu_base_clock=result.get('cpu_base_clock'),
        cpu_boost_clock=result.get('cpu_boost_clock'),
        cpu_tdp=result.get('cpu_tdp'),
        # GPU fields
        gpu_memory_size=result.get('gpu_memory_size'),
        gpu_memory_type=result.get('gpu_memory_type'),
        gpu_base_clock=result.get('gpu_base_clock'),
        gpu_boost_clock=result.get('gpu_boost_clock'),
        gpu_tdp=result.get('gpu_tdp'),
        # Motherboard fields
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
        # PSU fields
        psu_wattage=result.get('psu_wattage'),
        psu_efficiency=result.get('psu_efficiency'),
        psu_modular=result.get('psu_modular'),
        psu_form_factor=result.get('psu_form_factor'),
        # RAM fields
        ram_size=result.get('ram_size'),
        ram_type=result.get('ram_type'),
        ram_speed=result.get('ram_speed'),
        ram_cas_latency=result.get('ram_cas_latency'),
        # Storage fields
        storage_type=result.get('storage_type'),
        storage_interface=result.get('storage_interface'),
        storage_read_speed=result.get('storage_read_speed'),
        storage_write_speed=result.get('storage_write_speed'),
        # Cooler fields
        cooler_type=result.get('cooler_type'),
        cooler_tdp_rating=result.get('cooler_tdp_rating'),
        cooler_height=result.get('cooler_height'),
        cooler_fan_size=result.get('cooler_fan_size'),
        cooler_socket_support=result.get('cooler_socket_support'),
        # Case fields
        case_form_factor=result.get('case_form_factor'),
        case_max_gpu_length=result.get('case_max_gpu_length'),
        case_max_cooler_height=result.get('case_max_cooler_height'),
        # Fan fields
        fan_size=result.get('fan_size'),
        fan_rpm_max=result.get('fan_rpm_max'),
        fan_airflow=result.get('fan_airflow'),
    )
    db.session.add(spec)
    db.session.commit()
    
    # Get the actual source from the lookup result
    lookup_source = result.get('source', 'scraped')
    
    return jsonify({
        'found': True,
        'source': lookup_source,
        'spec_id': spec.id,
        'manufacturer': spec.manufacturer,
        'model': spec.model,
        'component_type': ct.name,
        # CPU fields
        'cpu_socket': spec.cpu_socket,
        'cpu_cores': spec.cpu_cores,
        'cpu_threads': spec.cpu_threads,
        'cpu_base_clock': float(spec.cpu_base_clock) if spec.cpu_base_clock else None,
        'cpu_boost_clock': float(spec.cpu_boost_clock) if spec.cpu_boost_clock else None,
        'cpu_tdp': spec.cpu_tdp,
        # GPU fields
        'gpu_memory_size': spec.gpu_memory_size,
        'gpu_memory_type': spec.gpu_memory_type,
        'gpu_base_clock': spec.gpu_base_clock,
        'gpu_boost_clock': spec.gpu_boost_clock,
        'gpu_tdp': spec.gpu_tdp,
        # Motherboard fields
        'mobo_socket': spec.mobo_socket,
        'mobo_chipset': spec.mobo_chipset,
        'mobo_form_factor': spec.mobo_form_factor,
        'mobo_memory_slots': spec.mobo_memory_slots,
        'mobo_memory_type': spec.mobo_memory_type,
        'mobo_max_memory': spec.mobo_max_memory,
        'mobo_pcie_x16_slots': spec.mobo_pcie_x16_slots,
        # PSU fields
        'psu_wattage': spec.psu_wattage,
        'psu_efficiency': spec.psu_efficiency,
        'psu_modular': spec.psu_modular,
        'psu_form_factor': spec.psu_form_factor,
        # RAM fields
        'ram_size': spec.ram_size,
        'ram_type': spec.ram_type,
        'ram_speed': spec.ram_speed,
        'ram_cas_latency': spec.ram_cas_latency,
        # Storage fields
        'storage_type': spec.storage_type,
        'storage_interface': spec.storage_interface,
        'storage_read_speed': spec.storage_read_speed,
        'storage_write_speed': spec.storage_write_speed,
        # Cooler fields
        'cooler_type': spec.cooler_type,
        'cooler_tdp_rating': spec.cooler_tdp_rating,
        'cooler_height': spec.cooler_height,
        'cooler_fan_size': spec.cooler_fan_size,
        'cooler_socket_support': spec.cooler_socket_support,
        # Case fields
        'case_form_factor': spec.case_form_factor,
        'case_max_gpu_length': spec.case_max_gpu_length,
        'case_max_cooler_height': spec.case_max_cooler_height,
        # Fan fields
        'fan_size': spec.fan_size,
        'fan_rpm_max': spec.fan_rpm_max,
        'fan_airflow': float(spec.fan_airflow) if spec.fan_airflow else None,
    })


@bp.route('/specs')
def get_specs():
    """Get hardware specs with optional filtering."""
    component_type = request.args.get('type')
    manufacturer = request.args.get('manufacturer')
    search = request.args.get('q')
    limit = request.args.get('limit', 50, type=int)
    
    query = HardwareSpec.query
    
    if component_type:
        ct = ComponentType.query.filter_by(name=component_type).first()
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
    spec = HardwareSpec.query.get_or_404(id)
    
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
        data.update({
            'size': spec.ram_size,
            'type': spec.ram_type,
            'speed': spec.ram_speed,
            'cas_latency': spec.ram_cas_latency
        })
    
    return jsonify(data)


@bp.route('/inventory')
def get_inventory():
    """Get inventory items."""
    items = Inventory.query.all()
    
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
    
    item = Inventory(
        hardware_spec_id=data.get('hardware_spec_id'),
        component_type_id=data['component_type_id'],
        custom_name=data.get('custom_name'),
        custom_manufacturer=data.get('custom_manufacturer'),
        quantity=data.get('quantity', 1),
        purchase_price=data.get('purchase_price'),
        condition=data.get('condition', 'New'),
        location=data.get('location'),
        notes=data.get('notes'),
        status=data.get('status', 'Available')
    )
    
    db.session.add(item)
    db.session.commit()
    
    return jsonify({'id': item.id, 'message': 'Item added'}), 201


@bp.route('/hosts')
def get_hosts():
    """Get all hosts."""
    hosts = Host.query.all()
    
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
    types = ComponentType.query.all()
    return jsonify([{'id': t.id, 'name': t.name} for t in types])


@bp.route('/stats')
def get_stats():
    """Get dashboard statistics."""
    return jsonify({
        'total_specs': HardwareSpec.query.count(),
        'total_inventory_items': Inventory.query.count(),
        'total_quantity': db.session.query(db.func.sum(Inventory.quantity)).scalar() or 0,
        'total_hosts': Host.query.count(),
        'available_items': Inventory.query.filter_by(status='Available').count(),
        'in_use_items': Inventory.query.filter_by(status='In Use').count()
    })
