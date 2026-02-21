"""
Backup & Restore - Create timestamped backups and restore from them.
Supports local and NAS backups.
"""

import os
import json
import shutil
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from app import db
from app.models import Backup, Inventory, Host, HardwareSpec, ComponentType, BuildPlan

bp = Blueprint('backup', __name__)

# Default backup directory
BACKUP_DIR = os.environ.get('BACKUP_DIR', '/app/data/backups')
NAS_PATH = os.environ.get('NAS_BACKUP_PATH', '')


@bp.route('/')
def index():
    """Backup management page."""
    # List existing backups
    backups = Backup.query.order_by(Backup.created_at.desc()).all()
    
    # Check if NAS is configured
    nas_configured = bool(NAS_PATH)
    
    return render_template('backup/index.html', 
                         backups=backups, 
                         nas_configured=nas_configured,
                         nas_path=NAS_PATH)


@bp.route('/import-specs')
def import_specs():
    """Dedicated import specs page with templates."""
    return render_template('backup/import_specs.html')


@bp.route('/create', methods=['POST'])
def create_backup():
    """Create a new backup."""
    backup_type = request.form.get('type', 'Local')
    notes = request.form.get('notes', '')
    
    # Generate timestamp filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"techreadout_backup_{timestamp}.json"
    
    # Determine backup path
    if backup_type == 'NAS' and NAS_PATH:
        backup_path = os.path.join(NAS_PATH, filename)
    else:
        backup_type = 'Local'
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_path = os.path.join(BACKUP_DIR, filename)
    
    try:
        # Export all data
        data = export_all_data()
        
        # Write backup file
        with open(backup_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        # Get file size
        size = os.path.getsize(backup_path)
        
        # Record backup
        backup = Backup(
            filename=filename,
            filepath=backup_path,
            backup_type=backup_type,
            size_bytes=size,
            notes=notes
        )
        db.session.add(backup)
        db.session.commit()
        
        flash(f'{backup_type} backup created: {filename}', 'success')
        
    except Exception as e:
        flash(f'Backup failed: {str(e)}', 'error')
    
    return redirect(url_for('backup.index'))


@bp.route('/restore/<int:id>', methods=['POST'])
def restore_backup(id):
    """Restore from a backup."""
    backup = Backup.query.get_or_404(id)
    
    if not os.path.exists(backup.filepath):
        flash(f'Backup file not found: {backup.filepath}', 'error')
        return redirect(url_for('backup.index'))
    
    try:
        # Create safety backup first
        safety_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safety_filename = f"techreadout_pre_restore_{safety_timestamp}.json"
        safety_path = os.path.join(BACKUP_DIR, safety_filename)
        os.makedirs(BACKUP_DIR, exist_ok=True)
        
        safety_data = export_all_data()
        with open(safety_path, 'w') as f:
            json.dump(safety_data, f, indent=2, default=str)
        
        safety_backup = Backup(
            filename=safety_filename,
            filepath=safety_path,
            backup_type='Local',
            size_bytes=os.path.getsize(safety_path),
            notes='Auto-created before restore'
        )
        db.session.add(safety_backup)
        
        # Load backup data
        with open(backup.filepath, 'r') as f:
            data = json.load(f)
        
        # Import data
        import_all_data(data)
        
        db.session.commit()
        flash(f'Restored from backup: {backup.filename}', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Restore failed: {str(e)}', 'error')
    
    return redirect(url_for('backup.index'))


@bp.route('/delete/<int:id>', methods=['POST'])
def delete_backup(id):
    """Delete a backup record and optionally the file."""
    backup = Backup.query.get_or_404(id)
    
    delete_file = request.form.get('delete_file') == 'yes'
    
    if delete_file and os.path.exists(backup.filepath):
        try:
            os.remove(backup.filepath)
        except:
            pass
    
    db.session.delete(backup)
    db.session.commit()
    
    flash('Backup deleted.', 'info')
    return redirect(url_for('backup.index'))


@bp.route('/download/<int:id>')
def download_backup(id):
    """Download a backup file."""
    from flask import send_file
    backup = Backup.query.get_or_404(id)
    
    if not os.path.exists(backup.filepath):
        flash('Backup file not found.', 'error')
        return redirect(url_for('backup.index'))
    
    return send_file(backup.filepath, as_attachment=True, download_name=backup.filename)


@bp.route('/export-csv')
def export_csv():
    """Export inventory to CSV format (compatible with PowerShell version)."""
    import csv
    import io
    from flask import Response
    
    # Create a ZIP with all CSVs
    output = io.StringIO()
    
    # Export inventory items grouped by component type
    component_types = ComponentType.query.all()
    
    all_csv = {}
    for ct in component_types:
        items = Inventory.query.filter_by(component_type_id=ct.id).all()
        if items:
            csv_output = io.StringIO()
            writer = csv.writer(csv_output, quoting=csv.QUOTE_ALL)
            
            # Write header based on component type
            headers = get_csv_headers(ct.name)
            writer.writerow(headers)
            
            # Write data
            for item in items:
                row = format_csv_row(item, ct.name)
                writer.writerow(row)
            
            all_csv[ct.name.lower()] = csv_output.getvalue()
    
    # Return as JSON with all CSVs
    return jsonify(all_csv)


@bp.route('/import', methods=['POST'])
def import_data():
    """Import data from uploaded file."""
    if 'file' not in request.files:
        flash('No file uploaded.', 'error')
        return redirect(url_for('backup.index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('backup.index'))
    
    try:
        if file.filename.endswith('.json'):
            data = json.load(file)
            import_all_data(data)
            flash('Data imported successfully!', 'success')
        else:
            flash('Unsupported file format. Use JSON.', 'error')
    except Exception as e:
        flash(f'Import failed: {str(e)}', 'error')
    
    return redirect(url_for('backup.index'))


def export_all_data():
    """Export all database data to a dictionary."""
    data = {
        'exported_at': datetime.utcnow().isoformat(),
        'version': '1.0',
        'component_types': [],
        'hardware_specs': [],
        'inventory': [],
        'hosts': [],
        'build_plans': []
    }
    
    # Component types
    for ct in ComponentType.query.all():
        data['component_types'].append({
            'id': ct.id,
            'name': ct.name
        })
    
    # Hardware specs
    for spec in HardwareSpec.query.all():
        data['hardware_specs'].append({
            'id': spec.id,
            'component_type': spec.component_type.name,
            'manufacturer': spec.manufacturer,
            'model': spec.model,
            'cpu_socket': spec.cpu_socket,
            'cpu_cores': spec.cpu_cores,
            'cpu_threads': spec.cpu_threads,
            'cpu_base_clock': float(spec.cpu_base_clock) if spec.cpu_base_clock else None,
            'cpu_boost_clock': float(spec.cpu_boost_clock) if spec.cpu_boost_clock else None,
            'cpu_tdp': spec.cpu_tdp,
            'gpu_memory_size': spec.gpu_memory_size,
            'gpu_memory_type': spec.gpu_memory_type,
            'gpu_tdp': spec.gpu_tdp,
            'ram_size': spec.ram_size,
            'ram_type': spec.ram_type,
            'ram_speed': spec.ram_speed,
            'source_url': spec.source_url
        })
    
    # Inventory
    for item in Inventory.query.all():
        data['inventory'].append({
            'id': item.id,
            'component_type': item.component_type.name,
            'hardware_spec_id': item.hardware_spec_id,
            'custom_name': item.custom_name,
            'custom_manufacturer': item.custom_manufacturer,
            'quantity': item.quantity,
            'purchase_date': item.purchase_date.isoformat() if item.purchase_date else None,
            'purchase_price': float(item.purchase_price) if item.purchase_price else None,
            'condition': item.item_condition,
            'location': item.location,
            'notes': item.notes,
            'status': item.status,
            'assigned_to_host_id': item.assigned_to_host_id,
            'sale_date': item.sale_date.isoformat() if item.sale_date else None,
            'sale_price': float(item.sale_price) if item.sale_price else None,
            'sold_to': item.sold_to
        })
    
    # Hosts
    for host in Host.query.all():
        data['hosts'].append({
            'id': host.id,
            'hostname': host.hostname,
            'description': host.description,
            'purpose': host.purpose,
            'os': host.os,
            'ip_address': host.ip_address,
            'mac_address': host.mac_address,
            'status': host.status
        })
    
    # Build plans
    for plan in BuildPlan.query.all():
        plan_data = {
            'id': plan.id,
            'name': plan.name,
            'description': plan.description,
            'status': plan.status,
            'min_ram_gb': plan.min_ram_gb,
            'min_vram_gb': plan.min_vram_gb,
            'cpu_socket': plan.cpu_socket,
            'use_case': plan.use_case,
            'budget': float(plan.budget) if plan.budget else None,
            'components': []
        }
        for comp in plan.components:
            plan_data['components'].append({
                'inventory_id': comp.inventory_id,
                'component_type': comp.component_type.name if comp.component_type else None,
                'quantity': comp.quantity
            })
        data['build_plans'].append(plan_data)
    
    return data


def import_all_data(data):
    """Import data from a dictionary (backup or migration)."""
    # This preserves existing data and adds/updates from import
    
    # Import component types first
    type_map = {}
    for ct_data in data.get('component_types', []):
        ct = ComponentType.query.filter_by(name=ct_data['name']).first()
        if not ct:
            ct = ComponentType(name=ct_data['name'])
            db.session.add(ct)
            db.session.flush()
        type_map[ct_data.get('id', ct_data['name'])] = ct.id
    
    # Import hardware specs
    spec_map = {}
    for spec_data in data.get('hardware_specs', []):
        ct_id = type_map.get(spec_data.get('component_type'))
        if not ct_id:
            ct = ComponentType.query.filter_by(name=spec_data.get('component_type')).first()
            ct_id = ct.id if ct else None
        
        if ct_id:
            existing = HardwareSpec.query.filter_by(
                model=spec_data['model'],
                manufacturer=spec_data.get('manufacturer')
            ).first()
            
            if not existing:
                spec = HardwareSpec(
                    component_type_id=ct_id,
                    manufacturer=spec_data.get('manufacturer'),
                    model=spec_data['model'],
                    cpu_socket=spec_data.get('cpu_socket'),
                    cpu_cores=spec_data.get('cpu_cores'),
                    cpu_threads=spec_data.get('cpu_threads'),
                    cpu_base_clock=spec_data.get('cpu_base_clock'),
                    cpu_boost_clock=spec_data.get('cpu_boost_clock'),
                    cpu_tdp=spec_data.get('cpu_tdp'),
                    gpu_memory_size=spec_data.get('gpu_memory_size'),
                    gpu_memory_type=spec_data.get('gpu_memory_type'),
                    gpu_tdp=spec_data.get('gpu_tdp'),
                    ram_size=spec_data.get('ram_size'),
                    ram_type=spec_data.get('ram_type'),
                    ram_speed=spec_data.get('ram_speed'),
                    source_url=spec_data.get('source_url')
                )
                db.session.add(spec)
                db.session.flush()
                spec_map[spec_data.get('id')] = spec.id
            else:
                spec_map[spec_data.get('id')] = existing.id
    
    # Import hosts
    host_map = {}
    for host_data in data.get('hosts', []):
        existing = Host.query.filter_by(hostname=host_data['hostname']).first()
        if not existing:
            host = Host(
                hostname=host_data['hostname'],
                description=host_data.get('description'),
                purpose=host_data.get('purpose'),
                os=host_data.get('os'),
                ip_address=host_data.get('ip_address'),
                mac_address=host_data.get('mac_address'),
                status=host_data.get('status', 'Active')
            )
            db.session.add(host)
            db.session.flush()
            host_map[host_data.get('id')] = host.id
        else:
            host_map[host_data.get('id')] = existing.id
    
    # Import inventory
    for item_data in data.get('inventory', []):
        ct_name = item_data.get('component_type')
        ct = ComponentType.query.filter_by(name=ct_name).first()
        if not ct:
            continue
        
        # Check for duplicate
        existing = None
        if item_data.get('hardware_spec_id'):
            spec_id = spec_map.get(item_data['hardware_spec_id'], item_data['hardware_spec_id'])
            existing = Inventory.query.filter_by(
                hardware_spec_id=spec_id,
                component_type_id=ct.id
            ).first()
        elif item_data.get('custom_name'):
            existing = Inventory.query.filter_by(
                custom_name=item_data['custom_name'],
                custom_manufacturer=item_data.get('custom_manufacturer'),
                component_type_id=ct.id
            ).first()
        
        if existing:
            # Update quantity
            existing.quantity += item_data.get('quantity', 1)
        else:
            item = Inventory(
                component_type_id=ct.id,
                hardware_spec_id=spec_map.get(item_data.get('hardware_spec_id')),
                custom_name=item_data.get('custom_name'),
                custom_manufacturer=item_data.get('custom_manufacturer'),
                quantity=item_data.get('quantity', 1),
                purchase_price=item_data.get('purchase_price'),
                item_condition=item_data.get('condition', 'New'),
                location=item_data.get('location'),
                notes=item_data.get('notes'),
                status=item_data.get('status', 'Available'),
                assigned_to_host_id=host_map.get(item_data.get('assigned_to_host_id')),
                sale_price=item_data.get('sale_price'),
                sold_to=item_data.get('sold_to')
            )
            # Handle dates
            if item_data.get('purchase_date'):
                from datetime import datetime
                item.purchase_date = datetime.fromisoformat(item_data['purchase_date']).date()
            if item_data.get('sale_date'):
                from datetime import datetime
                item.sale_date = datetime.fromisoformat(item_data['sale_date']).date()
            db.session.add(item)
    
    db.session.commit()


def get_csv_headers(component_type):
    """Get CSV headers matching PowerShell version format."""
    headers = {
        'CPU': ['ID', 'Brand', 'Model', 'Socket', 'Cores', 'Threads', 'BaseClock', 'BoostClock', 'TDP', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'GPU': ['ID', 'Brand', 'Model', 'VRAM_GB', 'MemoryType', 'Power_W', 'Interface', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'RAM': ['ID', 'Brand', 'Model', 'Capacity', 'Speed', 'Type', 'Latency', 'Voltage', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'PSU': ['ID', 'Brand', 'Model', 'Wattage', 'Efficiency', 'Modular', 'FormFactor', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'Motherboard': ['ID', 'Brand', 'Model', 'Socket', 'Chipset', 'FormFactor', 'MemorySlots', 'MaxMemory', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'Storage': ['ID', 'Brand', 'Model', 'Capacity', 'Interface', 'Type', 'FormFactor', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'Cooler': ['ID', 'Brand', 'Model', 'Type', 'SocketSupport', 'TDPRating', 'Height', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'Case': ['ID', 'Brand', 'Model', 'FormFactor', 'Type', 'Color', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
        'NIC': ['ID', 'Brand', 'Model', 'Speed', 'Interface', 'Ports', 'Quantity', 'Status', 'PurchaseDate', 'PurchasePrice', 'Notes'],
    }
    return headers.get(component_type, ['ID', 'Name', 'Quantity', 'Status', 'Notes'])


def format_csv_row(item, component_type):
    """Format inventory item as CSV row matching PowerShell format."""
    # Base fields
    row = [item.id]
    
    # Get manufacturer and model
    if item.hardware_spec:
        row.append(item.hardware_spec.manufacturer or '')
        row.append(item.hardware_spec.model or '')
    else:
        row.append(item.custom_manufacturer or '')
        row.append(item.custom_name or '')
    
    # Type-specific fields would go here
    # For now, just add quantity, status, dates, notes
    row.extend([
        item.quantity,
        item.status,
        item.purchase_date.isoformat() if item.purchase_date else '',
        float(item.purchase_price) if item.purchase_price else '',
        item.notes or ''
    ])
    
    return row


@bp.route('/import-specs-json', methods=['POST'])
def import_specs_json():
    """
    Import hardware specs from JSON - allows importing specs without using API credits.
    Users can get specs from Claude, ChatGPT, or any other source.
    Optionally also creates inventory items.
    """
    specs_json = request.form.get('specs_json', '').strip()
    create_inventory = request.form.get('create_inventory') == '1'
    
    if not specs_json:
        flash('No JSON provided', 'danger')
        return redirect(url_for('backup.import_specs'))
    
    try:
        data = json.loads(specs_json)
        
        # Handle both single object and array
        if isinstance(data, dict):
            specs_list = [data]
        elif isinstance(data, list):
            specs_list = data
        else:
            flash('JSON must be an object or array of objects', 'danger')
            return redirect(url_for('backup.index'))
        
        imported = 0
        skipped = 0
        errors = []
        
        for spec_data in specs_list:
            try:
                # Get or create component type
                ct_name = spec_data.get('component_type', 'Other')
                ct = ComponentType.query.filter_by(name=ct_name).first()
                if not ct:
                    ct = ComponentType(name=ct_name)
                    db.session.add(ct)
                    db.session.flush()
                
                # Check if spec already exists
                model = spec_data.get('model', '')
                manufacturer = spec_data.get('manufacturer', '')
                
                if not model:
                    errors.append(f"Skipped entry without model name")
                    skipped += 1
                    continue
                
                existing = HardwareSpec.query.filter(
                    HardwareSpec.model.ilike(model),
                    HardwareSpec.component_type_id == ct.id
                ).first()
                
                if existing:
                    errors.append(f"Duplicate: {manufacturer} {model}")
                    skipped += 1
                    continue
                
                # Create new spec
                print(f"[Import] Creating spec: {manufacturer} {model}")
                spec = HardwareSpec(
                    component_type_id=ct.id,
                    manufacturer=manufacturer,
                    model=model,
                    source_url=spec_data.get('source_url', 'JSON Import'),
                    raw_data=spec_data,
                    # CPU fields
                    cpu_socket=spec_data.get('cpu_socket'),
                    cpu_cores=spec_data.get('cpu_cores'),
                    cpu_threads=spec_data.get('cpu_threads'),
                    cpu_base_clock=spec_data.get('cpu_base_clock'),
                    cpu_boost_clock=spec_data.get('cpu_boost_clock'),
                    cpu_tdp=spec_data.get('cpu_tdp'),
                    # GPU fields
                    gpu_memory_size=spec_data.get('gpu_memory_size'),
                    gpu_memory_type=spec_data.get('gpu_memory_type'),
                    gpu_base_clock=spec_data.get('gpu_base_clock'),
                    gpu_boost_clock=spec_data.get('gpu_boost_clock'),
                    gpu_tdp=spec_data.get('gpu_tdp'),
                    # Motherboard fields
                    mobo_socket=spec_data.get('mobo_socket'),
                    mobo_chipset=spec_data.get('mobo_chipset'),
                    mobo_form_factor=spec_data.get('mobo_form_factor'),
                    mobo_memory_slots=spec_data.get('mobo_memory_slots'),
                    mobo_memory_type=spec_data.get('mobo_memory_type'),
                    mobo_max_memory=spec_data.get('mobo_max_memory'),
                    mobo_pcie_x16_slots=spec_data.get('mobo_pcie_x16_slots'),
                    mobo_pcie_x4_slots=spec_data.get('mobo_pcie_x4_slots'),
                    mobo_pcie_x1_slots=spec_data.get('mobo_pcie_x1_slots'),
                    mobo_m2_slots=spec_data.get('mobo_m2_slots'),
                    mobo_sata_ports=spec_data.get('mobo_sata_ports'),
                    # PSU fields
                    psu_wattage=spec_data.get('psu_wattage'),
                    psu_efficiency=spec_data.get('psu_efficiency'),
                    psu_modular=spec_data.get('psu_modular'),
                    psu_form_factor=spec_data.get('psu_form_factor'),
                    # RAM fields
                    ram_size=spec_data.get('ram_size'),
                    ram_type=spec_data.get('ram_type'),
                    ram_speed=spec_data.get('ram_speed'),
                    ram_cas_latency=spec_data.get('ram_cas_latency'),
                    ram_modules=spec_data.get('ram_modules'),
                    # Storage fields
                    storage_capacity=spec_data.get('storage_capacity'),
                    storage_type=spec_data.get('storage_type'),
                    storage_interface=spec_data.get('storage_interface'),
                    storage_form_factor=spec_data.get('storage_form_factor'),
                    storage_read_speed=spec_data.get('storage_read_speed'),
                    storage_write_speed=spec_data.get('storage_write_speed'),
                    # Cooler fields
                    cooler_type=spec_data.get('cooler_type'),
                    cooler_fan_size=spec_data.get('cooler_fan_size'),
                    cooler_height=spec_data.get('cooler_height'),
                    cooler_tdp_rating=spec_data.get('cooler_tdp_rating'),
                    cooler_socket_support=spec_data.get('cooler_socket_support'),
                    # Case fields
                    case_form_factor=spec_data.get('case_form_factor'),
                    case_type=spec_data.get('case_type'),
                    case_max_gpu_length=spec_data.get('case_max_gpu_length'),
                    case_max_cooler_height=spec_data.get('case_max_cooler_height'),
                )
                
                db.session.add(spec)
                db.session.flush()  # Get the spec ID
                imported += 1
                
                # Optionally create inventory item
                if create_inventory:
                    inventory_item = Inventory(
                        component_type_id=ct.id,
                        hardware_spec_id=spec.id,
                        custom_name=model,
                        custom_manufacturer=manufacturer,
                        status='Available'
                    )
                    db.session.add(inventory_item)
                
            except Exception as e:
                errors.append(f"Error: {spec_data.get('model', 'unknown')}: {str(e)}")
                skipped += 1
        
        db.session.commit()
        
        if imported > 0:
            msg = f'Successfully imported {imported} spec(s)!'
            if create_inventory:
                msg += f' Also created {imported} inventory item(s).'
            flash(msg, 'success')
        if skipped > 0:
            # Show all skip reasons
            flash(f'Skipped {skipped}: ' + '; '.join(errors), 'warning')
        if imported == 0 and skipped == 0:
            flash('No specs to import', 'info')
            
    except json.JSONDecodeError as e:
        flash(f'Invalid JSON: {str(e)}', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Import error: {str(e)}', 'danger')
    
    return redirect(url_for('backup.import_specs'))
