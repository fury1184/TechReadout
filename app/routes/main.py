import csv
import io
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, jsonify
from app import db
import os
from app.models import ComponentType, HardwareSpec, Inventory, Host, AppSetting, LookupCache, PendingReview, PriceCache, BuildPlanComponent

bp = Blueprint('main', __name__)


@bp.route('/')
def dashboard():
    """Dashboard with overview stats."""
    # Total value of non-sold/dead inventory with a price set
    inventory_value = db.session.query(
        db.func.sum(Inventory.purchase_price * Inventory.quantity)
    ).filter(
        Inventory.status.notin_(['Sold', 'Dead']),
        Inventory.purchase_price.isnot(None)
    ).scalar() or 0

    stats = {
        'total_specs': HardwareSpec.query.count(),
        'total_inventory': db.session.query(db.func.sum(Inventory.quantity)).scalar() or 0,
        'total_hosts': Host.query.count(),
        'available_items': Inventory.query.filter(Inventory.status.in_(['Verified', 'Unverified'])).count(),
        'inventory_value': float(inventory_value),
        'pending_reviews': db.session.query(PendingReview).filter_by(status='Pending').count(),
    }

    # Component breakdown for chart
    component_counts = db.session.query(
        ComponentType.name,
        db.func.count(Inventory.id)
    ).join(Inventory).group_by(ComponentType.name).all()

    # Recent additions
    recent_items = Inventory.query.order_by(Inventory.created_at.desc()).limit(5).all()

    return render_template('dashboard.html', stats=stats, component_counts=component_counts,
                           recent_items=recent_items)


@bp.route('/inventory')
def inventory_list():
    """List all inventory items."""
    component_type = request.args.get('type')
    status = request.args.get('status')
    assigned = request.args.get('assigned')
    show_all = request.args.get('show_all')  # Include sold/disposed
    
    query = Inventory.query
    
    if component_type:
        ct = ComponentType.query.filter_by(name=component_type).first()
        if ct:
            query = query.filter_by(component_type_id=ct.id)
    
    if status:
        query = query.filter_by(status=status)
    elif not show_all:
        # By default, hide Sold and Dead
        query = query.filter(Inventory.status.notin_(['Sold', 'Dead']))
    
    if assigned == 'assigned':
        query = query.filter(Inventory.assigned_to_host_id.isnot(None))
    elif assigned == 'unassigned':
        query = query.filter(Inventory.assigned_to_host_id.is_(None))
    
    items = query.order_by(Inventory.created_at.desc()).all()
    component_types = ComponentType.query.all()
    
    # Get counts for badges
    sold_count = Inventory.query.filter_by(status='Sold').count()
    
    return render_template('inventory/list.html', 
                         items=items, 
                         component_types=component_types,
                         current_type=component_type,
                         current_status=status,
                         current_assigned=assigned,
                         show_all=show_all,
                         sold_count=sold_count)


@bp.route('/inventory/export')
def inventory_export():
    """Export inventory to CSV. Respects same filters as inventory list."""
    component_type = request.args.get('type')
    status = request.args.get('status')
    assigned = request.args.get('assigned')
    show_all = request.args.get('show_all')

    query = Inventory.query

    if component_type:
        ct = ComponentType.query.filter_by(name=component_type).first()
        if ct:
            query = query.filter_by(component_type_id=ct.id)

    if status:
        query = query.filter_by(status=status)
    elif not show_all:
        query = query.filter(Inventory.status.notin_(['Sold', 'Dead']))

    if assigned == 'assigned':
        query = query.filter(Inventory.assigned_to_host_id.isnot(None))
    elif assigned == 'unassigned':
        query = query.filter(Inventory.assigned_to_host_id.is_(None))

    items = query.order_by(Inventory.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'ID', 'Name', 'Manufacturer', 'Model', 'Component Type',
        'Quantity', 'Condition', 'Status',
        'Purchase Price (per unit)', 'Total Cost', 'Purchase Date',
        'Location', 'Notes', 'Assigned Host',
        'Sale Price', 'Sale Date', 'Sold To', 'Profit'
    ])

    for item in items:
        manufacturer = item.custom_manufacturer or (item.hardware_spec.manufacturer if item.hardware_spec else '')
        model = item.custom_name or (item.hardware_spec.model if item.hardware_spec else '')
        total_cost = round(float(item.purchase_price) * item.quantity, 2) if item.purchase_price else ''
        profit = round(item.profit, 2) if item.profit is not None else ''

        writer.writerow([
            item.id,
            item.display_name,
            manufacturer,
            model,
            item.component_type.name,
            item.quantity,
            item.item_condition,
            item.status,
            float(item.purchase_price) if item.purchase_price else '',
            total_cost,
            item.purchase_date.strftime('%Y-%m-%d') if item.purchase_date else '',
            item.location or '',
            item.notes or '',
            item.assigned_host.hostname if item.assigned_host else '',
            float(item.sale_price) if item.sale_price else '',
            item.sale_date.strftime('%Y-%m-%d') if item.sale_date else '',
            item.sold_to or '',
            profit,
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=techreadout_inventory.csv'}
    )


@bp.route('/inventory/add', methods=['GET', 'POST'])
def inventory_add():
    """Add new inventory item."""
    if request.method == 'POST':
        spec_id = request.form.get('hardware_spec_id')
        
        # Get custom name - required if no spec
        custom_name = request.form.get('custom_name', '').strip()
        custom_manufacturer = request.form.get('custom_manufacturer', '').strip()
        component_type_id = request.form['component_type_id']
        
        # If no spec_id but manual specs provided, create a spec entry
        if not spec_id:
            ct = ComponentType.query.get(component_type_id)
            ct_name = ct.name if ct else None
            
            # Check if any manual specs were provided
            has_manual_specs = False
            manual_spec_fields = [
                'cpu_cores', 'cpu_threads', 'cpu_base_clock', 'cpu_boost_clock', 'cpu_tdp', 'cpu_socket',
                'gpu_memory_gb', 'gpu_memory_type', 'gpu_base_clock', 'gpu_boost_clock', 'gpu_tdp',
                'mobo_socket', 'mobo_chipset', 'mobo_form_factor', 'mobo_memory_type', 'mobo_memory_slots', 'mobo_m2_slots',
                'psu_wattage', 'psu_efficiency', 'psu_modular', 'psu_form_factor',
                'ram_capacity', 'ram_type', 'ram_speed', 'ram_timings',
                'storage_type', 'storage_interface', 'storage_read_speed', 'storage_write_speed',
                'cooler_type', 'cooler_fan_size', 'cooler_height', 'cooler_tdp_rating', 'cooler_socket_support',
                'case_form_factor', 'case_max_gpu_length', 'case_max_cooler_height',
                'fan_size', 'fan_rpm_max', 'fan_airflow'
            ]
            
            for field in manual_spec_fields:
                if request.form.get(field):
                    has_manual_specs = True
                    break
            
            if has_manual_specs and custom_name:
                # Create a new HardwareSpec with manual data
                spec = HardwareSpec(
                    component_type_id=component_type_id,
                    manufacturer=custom_manufacturer or None,
                    model=custom_name,
                    source_url=None,
                    raw_data={'source': 'manual_entry'},
                    # CPU fields
                    cpu_cores=int(request.form.get('cpu_cores')) if request.form.get('cpu_cores') else None,
                    cpu_threads=int(request.form.get('cpu_threads')) if request.form.get('cpu_threads') else None,
                    cpu_base_clock=float(request.form.get('cpu_base_clock')) if request.form.get('cpu_base_clock') else None,
                    cpu_boost_clock=float(request.form.get('cpu_boost_clock')) if request.form.get('cpu_boost_clock') else None,
                    cpu_tdp=int(request.form.get('cpu_tdp')) if request.form.get('cpu_tdp') else None,
                    cpu_socket=request.form.get('cpu_socket') or None,
                    # GPU fields
                    gpu_memory_size=int(request.form.get('gpu_memory_gb')) * 1024 if request.form.get('gpu_memory_gb') else None,  # Convert GB to MB
                    gpu_memory_type=request.form.get('gpu_memory_type') or None,
                    gpu_base_clock=int(request.form.get('gpu_base_clock')) if request.form.get('gpu_base_clock') else None,
                    gpu_boost_clock=int(request.form.get('gpu_boost_clock')) if request.form.get('gpu_boost_clock') else None,
                    gpu_tdp=int(request.form.get('gpu_tdp')) if request.form.get('gpu_tdp') else None,
                    # Motherboard fields
                    mobo_socket=request.form.get('mobo_socket') or None,
                    mobo_chipset=request.form.get('mobo_chipset') or None,
                    mobo_form_factor=request.form.get('mobo_form_factor') or None,
                    mobo_memory_type=request.form.get('mobo_memory_type') or None,
                    mobo_memory_slots=int(request.form.get('mobo_memory_slots')) if request.form.get('mobo_memory_slots') else None,
                    mobo_m2_slots=int(request.form.get('mobo_m2_slots')) if request.form.get('mobo_m2_slots') else None,
                    # PSU fields
                    psu_wattage=int(request.form.get('psu_wattage')) if request.form.get('psu_wattage') else None,
                    psu_efficiency=request.form.get('psu_efficiency') or None,
                    psu_modular=request.form.get('psu_modular') or None,
                    psu_form_factor=request.form.get('psu_form_factor') or None,
                    # RAM fields
                    ram_size=int(request.form.get('ram_capacity')) if request.form.get('ram_capacity') else None,
                    ram_type=request.form.get('ram_type') or None,
                    ram_speed=int(request.form.get('ram_speed')) if request.form.get('ram_speed') else None,
                    ram_cas_latency=request.form.get('ram_timings') or None,
                    # Storage fields
                    storage_type=request.form.get('storage_type') or None,
                    storage_interface=request.form.get('storage_interface') or None,
                    storage_read_speed=int(request.form.get('storage_read_speed')) if request.form.get('storage_read_speed') else None,
                    storage_write_speed=int(request.form.get('storage_write_speed')) if request.form.get('storage_write_speed') else None,
                    # Cooler fields
                    cooler_type=request.form.get('cooler_type') or None,
                    cooler_fan_size=int(request.form.get('cooler_fan_size')) if request.form.get('cooler_fan_size') else None,
                    cooler_height=int(request.form.get('cooler_height')) if request.form.get('cooler_height') else None,
                    cooler_tdp_rating=int(request.form.get('cooler_tdp_rating')) if request.form.get('cooler_tdp_rating') else None,
                    cooler_socket_support=request.form.get('cooler_socket_support') or None,
                    # Case fields
                    case_form_factor=request.form.get('case_form_factor') or None,
                    case_max_gpu_length=int(request.form.get('case_max_gpu_length')) if request.form.get('case_max_gpu_length') else None,
                    case_max_cooler_height=int(request.form.get('case_max_cooler_height')) if request.form.get('case_max_cooler_height') else None,
                    # Fan fields
                    fan_size=int(request.form.get('fan_size')) if request.form.get('fan_size') else None,
                    fan_rpm_max=int(request.form.get('fan_rpm_max')) if request.form.get('fan_rpm_max') else None,
                    fan_airflow=float(request.form.get('fan_airflow')) if request.form.get('fan_airflow') else None,
                )
                db.session.add(spec)
                db.session.flush()  # Get the ID
                spec_id = spec.id
        
        # If we have a spec_id, the custom fields were populated from lookup
        # but we still save them for display purposes
        
        item = Inventory(
            hardware_spec_id=spec_id if spec_id else None,
            component_type_id=component_type_id,
            custom_name=custom_name or None,
            custom_manufacturer=custom_manufacturer or None,
            quantity=int(request.form.get('quantity', 1)),
            purchase_price=request.form.get('purchase_price') or None,
            item_condition=request.form.get('condition', 'New'),
            location=request.form.get('location') or None,
            notes=request.form.get('notes') or None,
            status=request.form.get('status', 'Unverified')
        )
        
        # Check for duplicate and merge quantities only if truly identical
        # (same item, same condition, same purchase price)
        existing = None
        purchase_price = request.form.get('purchase_price')
        purchase_price_val = float(purchase_price) if purchase_price else None
        
        if spec_id:
            existing = Inventory.query.filter(
                Inventory.hardware_spec_id == spec_id,
                Inventory.component_type_id == component_type_id,
                Inventory.status == request.form.get('status', 'Unverified'),
                Inventory.item_condition == request.form.get('condition', 'New'),
                Inventory.purchase_price == purchase_price_val if purchase_price_val else Inventory.purchase_price.is_(None)
            ).first()
        elif custom_name:
            existing = Inventory.query.filter(
                Inventory.custom_name == custom_name,
                Inventory.custom_manufacturer == custom_manufacturer,
                Inventory.component_type_id == component_type_id,
                Inventory.status == request.form.get('status', 'Unverified'),
                Inventory.item_condition == request.form.get('condition', 'New'),
                Inventory.purchase_price == purchase_price_val if purchase_price_val else Inventory.purchase_price.is_(None)
            ).first()
        
        if existing:
            existing.quantity += int(request.form.get('quantity', 1))
            flash(f'Updated quantity for existing item (now {existing.quantity})', 'success')
        else:
            db.session.add(item)
            flash('Item added to inventory!', 'success')
        
        db.session.commit()

        # "Add Another" checkbox: stay on form with type/manufacturer pre-filled
        if request.form.get('add_another'):
            return redirect(url_for('main.inventory_add',
                                    prefill_type_id=component_type_id,
                                    prefill_manufacturer=custom_manufacturer or ''))
        return redirect(url_for('main.inventory_list'))
    
    component_types = ComponentType.query.all()
    ebay_enabled = (
        AppSetting.get_bool('ebay_pricing_enabled', False)
        and bool(os.environ.get('EBAY_APP_ID'))
        and bool(os.environ.get('EBAY_APP_SECRET'))
    )

    return render_template('inventory/add.html',
                         component_types=component_types,
                         prefill_type_id=request.args.get('prefill_type_id'),
                         prefill_manufacturer=request.args.get('prefill_manufacturer', ''),
                         ebay_enabled=ebay_enabled)


@bp.route('/inventory/<int:id>')
def inventory_detail(id):
    """View inventory item details and specs."""
    item = Inventory.query.get_or_404(id)
    ebay_enabled = (
        AppSetting.get_bool('ebay_pricing_enabled', False)
        and bool(os.environ.get('EBAY_APP_ID'))
        and bool(os.environ.get('EBAY_APP_SECRET'))
    )
    return render_template('inventory/detail.html', item=item, ebay_enabled=ebay_enabled)


@bp.route('/inventory/<int:id>/edit', methods=['GET', 'POST'])
def inventory_edit(id):
    """Edit inventory item."""
    item = Inventory.query.get_or_404(id)
    
    if request.method == 'POST':
        spec_id = request.form.get('hardware_spec_id')
        
        item.hardware_spec_id = spec_id if spec_id else None
        item.component_type_id = request.form['component_type_id']
        item.custom_name = request.form.get('custom_name') or None
        item.custom_manufacturer = request.form.get('custom_manufacturer') or None
        item.quantity = int(request.form.get('quantity', 1))
        new_price = request.form.get('purchase_price') or None
        item.purchase_price = new_price
        # Manual price entry always clears the eBay estimate flag
        if new_price is not None:
            item.price_is_estimate = False
        item.item_condition = request.form.get('condition', 'New')
        item.location = request.form.get('location') or None
        item.notes = request.form.get('notes') or None
        item.status = request.form.get('status', 'Unverified')
        item.assigned_to_host_id = request.form.get('assigned_to_host_id') or None
        
        db.session.commit()
        flash('Item updated!', 'success')
        return redirect(url_for('main.inventory_list'))
    
    component_types = ComponentType.query.all()
    specs = HardwareSpec.query.order_by(HardwareSpec.manufacturer, HardwareSpec.model).all()
    # Active hosts, plus the item's current host even if it isn't Active
    # (e.g. a Planned host). Otherwise the current assignment wouldn't appear
    # as a selectable option and a plain save would silently unassign it.
    hosts = Host.query.filter_by(status='Active').all()
    if item.assigned_to_host_id and not any(h.id == item.assigned_to_host_id for h in hosts):
        current_host = Host.query.get(item.assigned_to_host_id)
        if current_host:
            hosts = sorted(hosts + [current_host], key=lambda h: h.hostname.lower())
    
    return render_template('inventory/edit.html', 
                         item=item,
                         component_types=component_types,
                         specs=specs,
                         hosts=hosts)


@bp.route('/inventory/<int:id>/delete', methods=['POST'])
def inventory_delete(id):
    """Delete inventory item."""
    item = Inventory.query.get_or_404(id)
    _delete_inventory_item(item)
    db.session.commit()
    flash('Item deleted.', 'info')
    return redirect(url_for('main.inventory_list'))


@bp.route('/inventory/<int:id>/split', methods=['POST'])
def inventory_split(id):
    """Split a qty>1 row into individual qty=1 rows."""
    item = Inventory.query.get_or_404(id)
    if item.quantity <= 1:
        flash('Nothing to split — quantity is already 1.', 'warning')
        return redirect(url_for('main.inventory_detail', id=id))

    original_qty = item.quantity
    item.quantity = 1
    for _ in range(original_qty - 1):
        new_item = Inventory(
            hardware_spec_id=item.hardware_spec_id,
            component_type_id=item.component_type_id,
            custom_name=item.custom_name,
            custom_manufacturer=item.custom_manufacturer,
            quantity=1,
            purchase_date=item.purchase_date,
            purchase_price=item.purchase_price,
            item_condition=item.item_condition,
            location=item.location,
            notes=item.notes,
            status=item.status,
            assigned_to_host_id=item.assigned_to_host_id,
        )
        db.session.add(new_item)

    db.session.commit()
    flash(f'Split into {original_qty} individual rows.', 'success')
    return redirect(url_for('main.inventory_list'))


@bp.route('/inventory/<int:id>/relookup', methods=['POST'])
def inventory_relookup(id):
    """Clear cache entries for this item's spec so next lookup re-scrapes."""
    item = Inventory.query.get_or_404(id)
    if not item.hardware_spec:
        flash('No spec linked to this item — nothing to re-lookup.', 'warning')
        return redirect(url_for('main.inventory_detail', id=id))

    spec = item.hardware_spec
    deleted = db.session.query(LookupCache).filter_by(spec_id=spec.id).delete()
    db.session.commit()
    flash(f'Cache cleared for "{spec.display_name}" '
          f'({deleted} entr{"ies" if deleted != 1 else "y"} removed). '
          f'Re-run lookup to refresh specs.', 'success')
    return redirect(url_for('main.inventory_detail', id=id))


@bp.route('/inventory/ebay-price-preview', methods=['GET'])
def inventory_ebay_price_preview():
    """Return an eBay price estimate for a query string (used by the add form).

    Query params:
        q  – free-text search query (model name)

    Returns JSON: {price, listing_count, cached, cached_at} or {error}.
    """
    if not AppSetting.get_bool('ebay_pricing_enabled', False):
        return jsonify({'error': 'eBay pricing is disabled.'}), 400

    if not os.environ.get('EBAY_APP_ID') or not os.environ.get('EBAY_APP_SECRET'):
        return jsonify({'error': 'eBay API credentials are not configured.'}), 400

    search_query = request.args.get('q', '').strip()
    if not search_query:
        return jsonify({'error': 'Missing query parameter q.'}), 400

    # Return cached result if still fresh
    cached = PriceCache.get_fresh(search_query)
    if cached:
        return jsonify({
            'price': float(cached.ebay_price),
            'listing_count': cached.listing_count,
            'cached': True,
            'cached_at': cached.fetched_at.strftime('%Y-%m-%d %H:%M'),
        })

    from app.scrapers import ebay as ebay_module
    try:
        result = ebay_module.fetch_ebay_price(search_query)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': f'eBay API error: {str(e)}'}), 502

    entry = PriceCache(
        search_query=search_query,
        ebay_price=result['price'],
        listing_count=result['listing_count'],
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({
        'price': result['price'],
        'listing_count': result['listing_count'],
        'cached': False,
        'cached_at': None,
    })


@bp.route('/inventory/<int:id>/ebay-price', methods=['POST'])
def inventory_fetch_ebay_price(id):
    """Fetch an eBay price estimate for an inventory item (JSON endpoint)."""
    if not AppSetting.get_bool('ebay_pricing_enabled', False):
        return jsonify({'error': 'eBay pricing is disabled. Enable it in Lookup Settings.'}), 400

    if not os.environ.get('EBAY_APP_ID') or not os.environ.get('EBAY_APP_SECRET'):
        return jsonify({'error': 'eBay API credentials are not configured (EBAY_APP_ID / EBAY_APP_SECRET).'}), 400

    item = Inventory.query.get_or_404(id)
    search_query = item.display_name

    # Return cached result if still fresh
    cached = PriceCache.get_fresh(search_query)
    if cached:
        return jsonify({
            'price': float(cached.ebay_price),
            'listing_count': cached.listing_count,
            'cached': True,
            'cached_at': cached.fetched_at.strftime('%Y-%m-%d %H:%M'),
        })

    # Hit the eBay API
    from app.scrapers import ebay as ebay_module
    try:
        result = ebay_module.fetch_ebay_price(search_query)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': f'eBay API error: {str(e)}'}), 502

    # Persist to cache
    entry = PriceCache(
        search_query=search_query,
        ebay_price=result['price'],
        listing_count=result['listing_count'],
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({
        'price': result['price'],
        'listing_count': result['listing_count'],
        'cached': False,
        'cached_at': None,
    })


@bp.route('/inventory/<int:id>/apply-ebay-price', methods=['POST'])
def inventory_apply_ebay_price(id):
    """Apply an eBay price estimate as the item's purchase price."""
    item = Inventory.query.get_or_404(id)
    raw_price = request.form.get('price', '').strip()
    try:
        price = float(raw_price)
        if price <= 0:
            raise ValueError
    except ValueError:
        flash('Invalid price value.', 'danger')
        return redirect(url_for('main.inventory_detail', id=id))

    item.purchase_price = price
    item.price_is_estimate = True
    db.session.commit()
    flash(f'Purchase price set to ${price:.2f} (eBay estimate \u2731).', 'success')
    return redirect(url_for('main.inventory_detail', id=id))


@bp.route('/inventory/<int:id>/assign', methods=['GET', 'POST'])
def inventory_assign(id):
    """Assign inventory item(s) to a host."""
    item = Inventory.query.get_or_404(id)
    
    if request.method == 'POST':
        host_id = request.form.get('host_id')
        assign_qty = int(request.form.get('quantity', 1))
        
        if assign_qty > item.quantity:
            flash('Cannot assign more than available quantity.', 'error')
            return redirect(url_for('main.inventory_assign', id=id))
        
        if assign_qty <= 0:
            flash('Quantity must be at least 1.', 'error')
            return redirect(url_for('main.inventory_assign', id=id))
        
        host = Host.query.get_or_404(host_id)
        
        if assign_qty == item.quantity:
            # Assign entire item
            item.assigned_to_host_id = host_id
            item.status = 'Installed'
        else:
            # Split: reduce original quantity, create new assigned row
            item.quantity -= assign_qty
            
            # Create new row for assigned items
            assigned_item = Inventory(
                hardware_spec_id=item.hardware_spec_id,
                component_type_id=item.component_type_id,
                custom_name=item.custom_name,
                custom_manufacturer=item.custom_manufacturer,
                quantity=assign_qty,
                purchase_date=item.purchase_date,
                purchase_price=item.purchase_price,
                item_condition=item.item_condition,
                location=item.location,
                notes=item.notes,
                status='Installed',
                assigned_to_host_id=host_id
            )
            db.session.add(assigned_item)
        
        db.session.commit()
        flash(f'Assigned {assign_qty}x {item.display_name} to {host.hostname}', 'success')
        return redirect(url_for('main.inventory_list'))
    
    # GET - show assign form
    hosts = Host.query.filter(Host.status.in_(['Active', 'Planned'])).order_by(Host.hostname).all()
    return render_template('inventory/assign.html', item=item, hosts=hosts)


@bp.route('/inventory/<int:id>/unassign', methods=['POST'])
def inventory_unassign(id):
    """Unassign inventory item from host, merge back to available pool."""
    item = Inventory.query.get_or_404(id)
    
    if not item.assigned_to_host_id:
        flash('Item is not assigned to any host.', 'warning')
        return redirect(url_for('main.inventory_list'))
    
    # Find matching verified item to merge with (must match price too)
    merge_query = Inventory.query.filter(
        Inventory.id != item.id,
        Inventory.component_type_id == item.component_type_id,
        Inventory.item_condition == item.item_condition,
        Inventory.status == 'Verified',
        Inventory.assigned_to_host_id.is_(None)
    )
    
    # Match by spec or custom name
    if item.hardware_spec_id:
        merge_query = merge_query.filter(Inventory.hardware_spec_id == item.hardware_spec_id)
    else:
        merge_query = merge_query.filter(
            Inventory.hardware_spec_id.is_(None),
            Inventory.custom_name == item.custom_name,
            Inventory.custom_manufacturer == item.custom_manufacturer
        )
    
    # Must match purchase price
    if item.purchase_price:
        merge_query = merge_query.filter(Inventory.purchase_price == item.purchase_price)
    else:
        merge_query = merge_query.filter(Inventory.purchase_price.is_(None))
    
    merge_target = merge_query.first()
    
    if merge_target:
        # Merge quantities
        merge_target.quantity += item.quantity
        db.session.delete(item)
        flash(f'Returned {item.display_name} to available inventory (merged).', 'success')
    else:
        # Just mark as verified (you just had eyes on it)
        item.assigned_to_host_id = None
        item.status = 'Verified'
        flash(f'Returned {item.display_name} to available inventory.', 'success')
    
    db.session.commit()
    return redirect(url_for('main.inventory_list'))


@bp.route('/hosts/<int:id>/part-out', methods=['POST'])
def hosts_part_out(id):
    """Part out a host - unassign all components and return to available."""
    host = Host.query.get_or_404(id)
    
    components = Inventory.query.filter_by(assigned_to_host_id=id).all()
    
    if not components:
        flash('No components assigned to this host.', 'warning')
        return redirect(url_for('main.hosts_detail', id=id))
    
    merged_count = 0
    returned_count = 0
    
    for item in components:
        # Items coming off a host are always Used, regardless of prior condition.
        item.item_condition = 'Used'

        # Find a matching unassigned Verified record to merge into.
        # Match key: component type + make/model (via spec or custom name) + notes.
        # Condition is NOT part of the key — we force Used on both sides, so the
        # existing record's condition is updated to Used when merging.
        merge_query = Inventory.query.filter(
            Inventory.id != item.id,
            Inventory.component_type_id == item.component_type_id,
            Inventory.status == 'Verified',
            Inventory.assigned_to_host_id.is_(None)
        )

        # Match by spec record or custom name+manufacturer
        if item.hardware_spec_id:
            merge_query = merge_query.filter(Inventory.hardware_spec_id == item.hardware_spec_id)
        else:
            merge_query = merge_query.filter(
                Inventory.hardware_spec_id.is_(None),
                Inventory.custom_name == item.custom_name,
                Inventory.custom_manufacturer == item.custom_manufacturer
            )

        # Notes must match exactly (both null, both empty, or same string)
        if item.notes:
            merge_query = merge_query.filter(Inventory.notes == item.notes)
        else:
            merge_query = merge_query.filter(
                (Inventory.notes == None) | (Inventory.notes == '')
            )

        merge_target = merge_query.first()

        if merge_target:
            # Weighted-average unit price across combined quantity.
            # Treat null price as 0 for averaging purposes.
            existing_price = float(merge_target.purchase_price or 0)
            incoming_price = float(item.purchase_price or 0)
            existing_qty   = merge_target.quantity or 1
            incoming_qty   = item.quantity or 1
            combined_qty   = existing_qty + incoming_qty

            if existing_price == 0 and incoming_price == 0:
                merged_price = None
            else:
                merged_price = round(
                    (existing_price * existing_qty + incoming_price * incoming_qty)
                    / combined_qty,
                    2
                )

            merge_target.quantity      = combined_qty
            merge_target.purchase_price = merged_price
            merge_target.item_condition = 'Used'
            db.session.delete(item)
            merged_count += 1
        else:
            item.assigned_to_host_id = None
            item.status = 'Verified'
            returned_count += 1
    
    # Update host status
    host.status = 'Inactive'
    
    db.session.commit()
    flash(f'Parted out {host.hostname}: {merged_count + returned_count} components returned to inventory.', 'success')
    return redirect(url_for('main.hosts_detail', id=id))


@bp.route('/inventory/<int:id>/sell', methods=['GET', 'POST'])
def inventory_sell(id):
    """Mark inventory item as sold."""
    item = Inventory.query.get_or_404(id)
    
    if item.status == 'Sold':
        flash('Item is already marked as sold.', 'warning')
        return redirect(url_for('main.inventory_list'))
    
    if item.assigned_to_host_id:
        flash('Cannot sell item that is assigned to a host. Unassign it first.', 'warning')
        return redirect(url_for('main.inventory_list'))
    
    if request.method == 'POST':
        sell_qty = int(request.form.get('quantity', item.quantity))
        
        if sell_qty > item.quantity:
            flash('Cannot sell more than available quantity.', 'error')
            return redirect(url_for('main.inventory_sell', id=id))
        
        sale_date_str = request.form.get('sale_date')
        sale_date = datetime.strptime(sale_date_str, '%Y-%m-%d').date() if sale_date_str else datetime.utcnow().date()
        sale_price = request.form.get('sale_price') or None
        sold_to = request.form.get('sold_to', '').strip() or None
        
        if sell_qty == item.quantity:
            # Sell entire item
            item.status = 'Sold'
            item.sale_date = sale_date
            item.sale_price = sale_price
            item.sold_to = sold_to
        else:
            # Split: reduce original quantity, create new sold row
            item.quantity -= sell_qty
            
            sold_item = Inventory(
                hardware_spec_id=item.hardware_spec_id,
                component_type_id=item.component_type_id,
                custom_name=item.custom_name,
                custom_manufacturer=item.custom_manufacturer,
                quantity=sell_qty,
                purchase_date=item.purchase_date,
                purchase_price=item.purchase_price,
                condition=item.item_condition,
                location=item.location,
                notes=item.notes,
                status='Sold',
                sale_date=sale_date,
                sale_price=sale_price,
                sold_to=sold_to
            )
            db.session.add(sold_item)
        
        db.session.commit()
        
        # Calculate profit if possible (purchase_price is per unit)
        profit_msg = ''
        if sale_price and item.purchase_price:
            profit = float(sale_price) - float(item.purchase_price) * sell_qty
            profit_msg = f' ({"+" if profit >= 0 else ""}${profit:.2f})'
        
        flash(f'Marked {sell_qty}x {item.display_name} as sold{profit_msg}', 'success')
        return redirect(url_for('main.inventory_list'))
    
    return render_template('inventory/sell.html', item=item, today=datetime.utcnow().strftime('%Y-%m-%d'))


@bp.route('/specs')
def specs_list():
    """List hardware specifications database."""
    component_type = request.args.get('type')
    manufacturer = request.args.get('manufacturer')
    search = request.args.get('q')
    
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
    
    specs = query.order_by(HardwareSpec.manufacturer, HardwareSpec.model).limit(200).all()
    component_types = ComponentType.query.all()
    
    # Get unique manufacturers
    manufacturers = db.session.query(HardwareSpec.manufacturer).distinct().order_by(HardwareSpec.manufacturer).all()
    manufacturers = [m[0] for m in manufacturers if m[0]]
    
    return render_template('specs/list.html',
                         specs=specs,
                         component_types=component_types,
                         manufacturers=manufacturers,
                         current_type=component_type,
                         current_manufacturer=manufacturer,
                         search=search)


def _delete_inventory_item(item):
    """Delete an inventory item, detaching any build plan references first.

    BuildPlanComponent.inventory_id is nullable — nulling it keeps the plan
    slot (component_type is preserved) but marks it unfulfilled, instead of
    silently removing pieces of a build plan.
    """
    BuildPlanComponent.query.filter_by(inventory_id=item.id).update(
        {'inventory_id': None}, synchronize_session=False
    )
    db.session.delete(item)


def _clean_spec_references(spec_id):
    """Remove/detach all non-inventory FK references to a spec.

    Must be called before deleting a HardwareSpec or MariaDB raises an
    IntegrityError. Covers lookup_cache.spec_id and
    pending_reviews.resolved_spec_id.
    """
    # Note: LookupCache and PendingReview both define a column named `query`,
    # which shadows Flask-SQLAlchemy's Model.query property — use db.session.query().
    db.session.query(LookupCache).filter_by(spec_id=spec_id).delete(synchronize_session=False)
    db.session.query(PendingReview).filter_by(resolved_spec_id=spec_id).update(
        {'resolved_spec_id': None}, synchronize_session=False
    )


@bp.route('/specs/<int:id>/delete-info')
def specs_delete_info(id):
    """Return JSON describing what a spec delete would affect (for the confirm modal)."""
    spec = HardwareSpec.query.get_or_404(id)
    items = Inventory.query.filter_by(hardware_spec_id=id).all()
    return jsonify({
        'spec_id': spec.id,
        'model': spec.display_name,
        'inventory': [
            {
                'id': item.id,
                'name': item.display_name,
                'status': item.status,
                'quantity': item.quantity,
                'assigned_host': item.assigned_host.hostname if item.assigned_host else None,
            }
            for item in items
        ],
    })


@bp.route('/specs/<int:id>/delete', methods=['POST'])
def specs_delete(id):
    """Delete a single spec entry.

    Form param `mode` controls handling of linked inventory items:
      - 'cascade': delete linked inventory items along with the spec
      - 'unlink':  keep inventory items but clear their hardware_spec_id
      - absent:    only delete if no inventory items are linked (legacy behavior)
    lookup_cache and pending_review references are always cleaned up first.
    """
    spec = HardwareSpec.query.get_or_404(id)
    model_name = spec.model
    mode = request.form.get('mode', '')

    linked_items = Inventory.query.filter_by(hardware_spec_id=id).all()

    try:
        if linked_items:
            if mode == 'cascade':
                for item in linked_items:
                    _delete_inventory_item(item)
            elif mode == 'unlink':
                for item in linked_items:
                    item.hardware_spec_id = None
                    # Preserve the display name so the item isn't left nameless
                    if not item.custom_name and spec.model:
                        item.custom_name = spec.model
                        if not item.custom_manufacturer and spec.manufacturer:
                            item.custom_manufacturer = spec.manufacturer
            else:
                flash(f'Cannot delete: {len(linked_items)} inventory item(s) use this spec. Remove or reassign them first.', 'danger')
                return redirect(url_for('main.specs_list'))

        _clean_spec_references(id)
        db.session.delete(spec)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Delete failed and was rolled back: {e}', 'danger')
        return redirect(url_for('main.specs_list'))

    if linked_items and mode == 'cascade':
        flash(f'Deleted spec "{model_name}" and {len(linked_items)} linked inventory item(s).', 'success')
    elif linked_items and mode == 'unlink':
        flash(f'Deleted spec "{model_name}". {len(linked_items)} inventory item(s) were unlinked and kept.', 'success')
    else:
        flash(f'Deleted spec: {model_name}', 'success')
    return redirect(url_for('main.specs_list'))


@bp.route('/specs/bulk-delete', methods=['POST'])
def specs_bulk_delete():
    """Bulk delete specs by IDs, source, or type.

    Form param `cascade_items` (checkbox): when set, specs that are in use
    by inventory items are deleted along with those items. When unset,
    in-use specs are skipped (legacy behavior).
    """
    spec_ids = request.form.get('spec_ids', '').strip()
    delete_source = request.form.get('delete_source', '').strip()
    delete_type = request.form.get('delete_type', '').strip()
    cascade_items = bool(request.form.get('cascade_items'))

    # Collect target specs
    if spec_ids:
        ids = [int(id.strip()) for id in spec_ids.split(',') if id.strip().isdigit()]
        specs = [s for s in (HardwareSpec.query.get(i) for i in ids) if s]
    elif delete_source:
        specs = HardwareSpec.query.filter(
            HardwareSpec.source_url.ilike(f'%{delete_source}%')
        ).all()
    elif delete_type:
        specs = HardwareSpec.query.filter_by(component_type_id=int(delete_type)).all()
    else:
        specs = []

    deleted_count = 0
    skipped_count = 0
    deleted_items = 0

    try:
        for spec in specs:
            linked = Inventory.query.filter_by(hardware_spec_id=spec.id).all()
            if linked and not cascade_items:
                skipped_count += 1
                continue
            for item in linked:
                _delete_inventory_item(item)
                deleted_items += 1
            _clean_spec_references(spec.id)
            db.session.delete(spec)
            deleted_count += 1

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Bulk delete failed and was rolled back: {e}', 'danger')
        return redirect(url_for('main.specs_list'))

    if deleted_count > 0:
        msg = f'Deleted {deleted_count} spec(s)'
        if deleted_items > 0:
            msg += f' and {deleted_items} linked inventory item(s)'
        flash(msg + '.', 'success')
    if skipped_count > 0:
        flash(f'Skipped {skipped_count} spec(s) that are in use by inventory items. '
              f'Check "Also delete linked inventory items" to remove them too.', 'warning')
    if deleted_count == 0 and skipped_count == 0:
        flash('No specs were deleted.', 'info')

    return redirect(url_for('main.specs_list'))


@bp.route('/hosts')
def hosts_list():
    """List all hosts."""
    hosts = Host.query.order_by(Host.hostname).all()

    build_costs = {}
    for host in hosts:
        total = sum(
            float(c.purchase_price) * c.quantity
            for c in host.components
            if c.purchase_price is not None
        )
        build_costs[host.id] = total if total > 0 else None

    return render_template('hosts/list.html', hosts=hosts, build_costs=build_costs)


@bp.route('/hosts/add', methods=['GET', 'POST'])
def hosts_add():
    """Add new host."""
    if request.method == 'POST':
        host = Host(
            hostname=request.form['hostname'],
            description=request.form.get('description') or None,
            purpose=request.form.get('purpose') or None,
            os=request.form.get('os') or None,
            ip_address=request.form.get('ip_address') or None,
            mac_address=request.form.get('mac_address') or None,
            status=request.form.get('status', 'Active')
        )
        
        db.session.add(host)
        db.session.commit()
        flash('Host added!', 'success')
        return redirect(url_for('main.hosts_list'))
    
    return render_template('hosts/add.html')


@bp.route('/hosts/compare')
def hosts_compare():
    """Compare two hosts side by side."""
    id_a = request.args.get('a', type=int)
    id_b = request.args.get('b', type=int)

    if not id_a or not id_b:
        flash('Select two hosts to compare.', 'warning')
        return redirect(url_for('main.hosts_list'))

    host_a = Host.query.get_or_404(id_a)
    host_b = Host.query.get_or_404(id_b)

    components_a = Inventory.query.filter_by(assigned_to_host_id=id_a).all()
    components_b = Inventory.query.filter_by(assigned_to_host_id=id_b).all()

    all_types = sorted(set(
        [c.component_type.name for c in components_a] +
        [c.component_type.name for c in components_b]
    ))

    def by_type(components):
        result = {}
        for c in components:
            result.setdefault(c.component_type.name, []).append(c)
        return result

    map_a = by_type(components_a)
    map_b = by_type(components_b)

    cost_a = sum(float(c.purchase_price) * c.quantity for c in components_a if c.purchase_price) or None
    cost_b = sum(float(c.purchase_price) * c.quantity for c in components_b if c.purchase_price) or None

    return render_template('hosts/compare.html',
                           host_a=host_a, host_b=host_b,
                           map_a=map_a, map_b=map_b,
                           all_types=all_types,
                           cost_a=cost_a, cost_b=cost_b)


@bp.route('/hosts/<int:id>')
def hosts_detail(id):
    """Host detail with assigned components."""
    host = Host.query.get_or_404(id)
    components = Inventory.query.filter_by(assigned_to_host_id=id).all()

    build_cost = sum(
        float(c.purchase_price) * c.quantity
        for c in components
        if c.purchase_price is not None
    )
    build_cost = build_cost if build_cost > 0 else None

    return render_template('hosts/detail.html', host=host, components=components, build_cost=build_cost)


@bp.route('/hosts/<int:id>/edit', methods=['GET', 'POST'])
def hosts_edit(id):
    """Edit host details."""
    host = Host.query.get_or_404(id)

    if request.method == 'POST':
        host.hostname    = request.form.get('hostname', '').strip()
        host.status      = request.form.get('status', 'Active')
        host.purpose     = request.form.get('purpose', '').strip() or None
        host.os          = request.form.get('os', '').strip() or None
        host.ip_address  = request.form.get('ip_address', '').strip() or None
        host.mac_address = request.form.get('mac_address', '').strip() or None
        host.description = request.form.get('description', '').strip() or None
        db.session.commit()
        flash('Host updated.', 'success')
        return redirect(url_for('main.hosts_detail', id=host.id))

    return render_template('hosts/edit.html', host=host)


# ── Cache Management ──────────────────────────────────────────────────────────

@bp.route('/cache')
def cache_list():
    """View and manage the lookup cache."""
    status_filter = request.args.get('status')
    type_filter   = request.args.get('type')
    page          = request.args.get('page', 1, type=int)

    query = db.session.query(LookupCache)
    if status_filter:
        query = query.filter(LookupCache.status == status_filter)
    if type_filter:
        query = query.filter(LookupCache.component_type == type_filter)

    entries = query.order_by(LookupCache.updated_at.desc()).paginate(
        page=page, per_page=50, error_out=False
    )

    miss_count  = db.session.query(LookupCache).filter_by(status='miss').count()
    total_count = db.session.query(LookupCache).count()

    component_types = sorted([
        t[0] for t in db.session.query(LookupCache.component_type).distinct().all()
    ])

    return render_template('cache/list.html',
                           entries=entries,
                           miss_count=miss_count,
                           total_count=total_count,
                           component_types=component_types,
                           current_status=status_filter,
                           current_type=type_filter,
                           now=datetime.utcnow())


@bp.route('/cache/clear-misses', methods=['POST'])
def cache_clear_misses():
    """Bulk-delete all miss entries."""
    deleted = db.session.query(LookupCache).filter_by(status='miss').delete()
    db.session.commit()
    flash(f'Cleared {deleted} miss entr{"ies" if deleted != 1 else "y"} from cache.', 'success')
    return redirect(url_for('main.cache_list'))


@bp.route('/cache/clear-old', methods=['POST'])
def cache_clear_old():
    """Delete cache entries older than 30 days."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    deleted = db.session.query(LookupCache).filter(LookupCache.updated_at < cutoff).delete()
    db.session.commit()
    flash(f'Cleared {deleted} stale entr{"ies" if deleted != 1 else "y"} (>30 days old).', 'success')
    return redirect(url_for('main.cache_list'))


@bp.route('/cache/<int:entry_id>/delete', methods=['POST'])
def cache_delete_entry(entry_id):
    """Delete a single cache entry."""
    entry = db.session.get(LookupCache, entry_id)
    if entry:
        label = entry.query
        db.session.delete(entry)
        db.session.commit()
        flash(f'Cache entry for "{label}" deleted.', 'success')
    return redirect(url_for('main.cache_list'))


# ── Scrape Review Queue ───────────────────────────────────────────────────────

@bp.route('/review/queue')
def review_queue():
    """View pending scrape reviews."""
    status_filter = request.args.get('status', 'Pending')
    page          = request.args.get('page', 1, type=int)

    query = db.session.query(PendingReview)
    if status_filter:
        query = query.filter(PendingReview.status == status_filter)

    reviews       = query.order_by(PendingReview.triggered_at.desc()).paginate(
        page=page, per_page=25, error_out=False
    )
    pending_count = db.session.query(PendingReview).filter_by(status='Pending').count()

    return render_template('review/queue.html',
                           reviews=reviews,
                           pending_count=pending_count,
                           current_status=status_filter)


@bp.route('/review/save', methods=['POST'])
def review_save():
    """Persist a triggered review from the add form (fire-and-forget JSON)."""
    data           = request.get_json(silent=True) or {}
    query_str      = data.get('query', '').strip()
    component_type = data.get('component_type', '').strip()
    candidates     = data.get('candidates', [])
    top_confidence = data.get('top_confidence')

    if not query_str or not component_type:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

    # Avoid duplicate pending entries for the same query+type
    existing = db.session.query(PendingReview).filter_by(
        query=query_str, component_type=component_type, status='Pending'
    ).first()

    if not existing:
        review = PendingReview(
            query=query_str,
            component_type=component_type,
            candidates=candidates,
            top_confidence=top_confidence,
        )
        db.session.add(review)
        db.session.commit()

    return jsonify({'status': 'ok'})


@bp.route('/review/<int:review_id>/accept', methods=['POST'])
def review_accept(review_id):
    """Accept a candidate from the review queue."""
    review = db.session.get(PendingReview, review_id)
    if not review:
        flash('Review not found.', 'danger')
        return redirect(url_for('main.review_queue'))

    spec_id = request.form.get('spec_id', type=int)
    review.status           = 'Accepted'
    review.resolved_spec_id = spec_id
    review.resolved_at      = datetime.utcnow()
    db.session.commit()
    flash(f'Review for "{review.query}" accepted.', 'success')
    return redirect(url_for('main.review_queue'))


@bp.route('/review/<int:review_id>/skip', methods=['POST'])
def review_skip(review_id):
    """Skip/dismiss a pending review."""
    review = db.session.get(PendingReview, review_id)
    if not review:
        flash('Review not found.', 'danger')
        return redirect(url_for('main.review_queue'))

    review.status      = 'Skipped'
    review.resolved_at = datetime.utcnow()
    db.session.commit()
    flash(f'Review for "{review.query}" dismissed.', 'info')
    return redirect(url_for('main.review_queue'))


@bp.route('/api/review-count')
def api_review_count():
    """Lightweight endpoint for the sidebar badge."""
    count = db.session.query(PendingReview).filter_by(status='Pending').count()
    return jsonify({'count': count})
