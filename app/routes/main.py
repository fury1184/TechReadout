from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import ComponentType, HardwareSpec, Inventory, Host, ScrapeJob

bp = Blueprint('main', __name__)


@bp.route('/')
def dashboard():
    """Dashboard with overview stats."""
    stats = {
        'total_specs': HardwareSpec.query.count(),
        'total_inventory': db.session.query(db.func.sum(Inventory.quantity)).scalar() or 0,
        'total_hosts': Host.query.count(),
        'available_items': Inventory.query.filter_by(status='Available').count(),
        'recent_scrapes': ScrapeJob.query.order_by(ScrapeJob.created_at.desc()).limit(5).all()
    }
    
    # Component breakdown
    component_counts = db.session.query(
        ComponentType.name,
        db.func.count(Inventory.id)
    ).join(Inventory).group_by(ComponentType.name).all()
    
    return render_template('dashboard.html', stats=stats, component_counts=component_counts)


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
        # By default, hide Sold and Disposed
        query = query.filter(Inventory.status.notin_(['Sold', 'Disposed']))
    
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
            status=request.form.get('status', 'Available')
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
                Inventory.status == request.form.get('status', 'Available'),
                Inventory.item_condition == request.form.get('condition', 'New'),
                Inventory.purchase_price == purchase_price_val if purchase_price_val else Inventory.purchase_price.is_(None)
            ).first()
        elif custom_name:
            existing = Inventory.query.filter(
                Inventory.custom_name == custom_name,
                Inventory.custom_manufacturer == custom_manufacturer,
                Inventory.component_type_id == component_type_id,
                Inventory.status == request.form.get('status', 'Available'),
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
        return redirect(url_for('main.inventory_list'))
    
    component_types = ComponentType.query.all()
    
    return render_template('inventory/add.html', 
                         component_types=component_types)


@bp.route('/inventory/<int:id>')
def inventory_detail(id):
    """View inventory item details and specs."""
    item = Inventory.query.get_or_404(id)
    return render_template('inventory/detail.html', item=item)


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
        item.purchase_price = request.form.get('purchase_price') or None
        item.item_condition = request.form.get('condition', 'New')
        item.location = request.form.get('location') or None
        item.notes = request.form.get('notes') or None
        item.status = request.form.get('status', 'Available')
        item.assigned_to_host_id = request.form.get('assigned_to_host_id') or None
        
        db.session.commit()
        flash('Item updated!', 'success')
        return redirect(url_for('main.inventory_list'))
    
    component_types = ComponentType.query.all()
    specs = HardwareSpec.query.order_by(HardwareSpec.manufacturer, HardwareSpec.model).all()
    hosts = Host.query.filter_by(status='Active').all()
    
    return render_template('inventory/edit.html', 
                         item=item,
                         component_types=component_types,
                         specs=specs,
                         hosts=hosts)


@bp.route('/inventory/<int:id>/delete', methods=['POST'])
def inventory_delete(id):
    """Delete inventory item."""
    item = Inventory.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Item deleted.', 'info')
    return redirect(url_for('main.inventory_list'))


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
            item.status = 'In Use'
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
                status='In Use',
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
    
    # Find matching available item to merge with (must match price too)
    merge_query = Inventory.query.filter(
        Inventory.id != item.id,
        Inventory.component_type_id == item.component_type_id,
        Inventory.item_condition == item.item_condition,
        Inventory.status == 'Available',
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
        # Just mark as available
        item.assigned_to_host_id = None
        item.status = 'Available'
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
        # Find matching available item to merge with (must match price too)
        merge_query = Inventory.query.filter(
            Inventory.id != item.id,
            Inventory.component_type_id == item.component_type_id,
            Inventory.item_condition == item.item_condition,
            Inventory.status == 'Available',
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
            merge_target.quantity += item.quantity
            db.session.delete(item)
            merged_count += 1
        else:
            item.assigned_to_host_id = None
            item.status = 'Available'
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
        
        from datetime import datetime
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
        
        # Calculate profit if possible
        profit_msg = ''
        if sale_price and item.purchase_price:
            profit = float(sale_price) - float(item.purchase_price)
            profit_msg = f' ({"+" if profit >= 0 else ""}${profit:.2f})'
        
        flash(f'Marked {sell_qty}x {item.display_name} as sold{profit_msg}', 'success')
        return redirect(url_for('main.inventory_list'))
    
    from datetime import datetime
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


@bp.route('/specs/<int:id>/delete', methods=['POST'])
def specs_delete(id):
    """Delete a single spec entry."""
    spec = HardwareSpec.query.get_or_404(id)
    model_name = spec.model
    
    # Check if any inventory items use this spec
    inventory_count = Inventory.query.filter_by(hardware_spec_id=id).count()
    if inventory_count > 0:
        flash(f'Cannot delete: {inventory_count} inventory item(s) use this spec. Remove or reassign them first.', 'danger')
        return redirect(url_for('main.specs_list'))
    
    db.session.delete(spec)
    db.session.commit()
    flash(f'Deleted spec: {model_name}', 'success')
    return redirect(url_for('main.specs_list'))


@bp.route('/specs/bulk-delete', methods=['POST'])
def specs_bulk_delete():
    """Bulk delete specs by IDs, source, or type."""
    spec_ids = request.form.get('spec_ids', '').strip()
    delete_source = request.form.get('delete_source', '').strip()
    delete_type = request.form.get('delete_type', '').strip()
    
    deleted_count = 0
    skipped_count = 0
    
    if spec_ids:
        # Delete selected specs
        ids = [int(id.strip()) for id in spec_ids.split(',') if id.strip().isdigit()]
        for spec_id in ids:
            spec = HardwareSpec.query.get(spec_id)
            if spec:
                # Check if used by inventory
                if Inventory.query.filter_by(hardware_spec_id=spec_id).count() > 0:
                    skipped_count += 1
                else:
                    db.session.delete(spec)
                    deleted_count += 1
    
    elif delete_source:
        # Delete by source
        specs = HardwareSpec.query.filter(
            HardwareSpec.source_url.ilike(f'%{delete_source}%')
        ).all()
        for spec in specs:
            if Inventory.query.filter_by(hardware_spec_id=spec.id).count() > 0:
                skipped_count += 1
            else:
                db.session.delete(spec)
                deleted_count += 1
    
    elif delete_type:
        # Delete by component type
        specs = HardwareSpec.query.filter_by(component_type_id=int(delete_type)).all()
        for spec in specs:
            if Inventory.query.filter_by(hardware_spec_id=spec.id).count() > 0:
                skipped_count += 1
            else:
                db.session.delete(spec)
                deleted_count += 1
    
    db.session.commit()
    
    if deleted_count > 0:
        flash(f'Deleted {deleted_count} spec(s).', 'success')
    if skipped_count > 0:
        flash(f'Skipped {skipped_count} spec(s) that are in use by inventory items.', 'warning')
    if deleted_count == 0 and skipped_count == 0:
        flash('No specs were deleted.', 'info')
    
    return redirect(url_for('main.specs_list'))


@bp.route('/hosts')
def hosts_list():
    """List all hosts."""
    hosts = Host.query.order_by(Host.hostname).all()
    return render_template('hosts/list.html', hosts=hosts)


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


@bp.route('/hosts/<int:id>')
def hosts_detail(id):
    """Host detail with assigned components."""
    host = Host.query.get_or_404(id)
    components = Inventory.query.filter_by(assigned_to_host_id=id).all()
    return render_template('hosts/detail.html', host=host, components=components)
