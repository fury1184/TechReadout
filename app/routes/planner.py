"""
Build Planner - Plan new builds from available inventory.
Checks compatibility, available parts, and requirements.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app import db
from app.models import (
    ComponentType, HardwareSpec, Inventory, Host, 
    BuildPlan, BuildPlanComponent
)
from sqlalchemy import func

bp = Blueprint('planner', __name__)


@bp.route('/')
def index():
    """List all build plans."""
    plans = BuildPlan.query.order_by(BuildPlan.updated_at.desc()).all()
    return render_template('planner/list.html', plans=plans)


@bp.route('/new', methods=['GET', 'POST'])
def new_plan():
    """Create a new build plan."""
    if request.method == 'POST':
        plan = BuildPlan(
            name=request.form['name'],
            description=request.form.get('description'),
            min_ram_gb=request.form.get('min_ram_gb') or None,
            min_vram_gb=request.form.get('min_vram_gb') or None,
            cpu_socket=request.form.get('cpu_socket') or None,
            use_case=request.form.get('use_case') or None,
            budget=request.form.get('budget') or None,
            status='Planning'
        )
        db.session.add(plan)
        db.session.commit()
        flash('Build plan created!', 'success')
        return redirect(url_for('planner.detail', id=plan.id))
    
    # Get available sockets from inventory
    sockets = db.session.query(HardwareSpec.cpu_socket).filter(
        HardwareSpec.cpu_socket.isnot(None)
    ).distinct().all()
    sockets = [s[0] for s in sockets if s[0]]
    
    return render_template('planner/new.html', sockets=sockets)


@bp.route('/<int:id>')
def detail(id):
    """View build plan with compatibility check."""
    plan = BuildPlan.query.get_or_404(id)
    
    # Get available parts analysis
    availability = check_availability(plan)
    
    # Get assigned components
    assigned = BuildPlanComponent.query.filter_by(build_plan_id=id).all()
    
    return render_template('planner/detail.html', 
                         plan=plan, 
                         availability=availability,
                         assigned=assigned)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
def edit_plan(id):
    """Edit build plan."""
    plan = BuildPlan.query.get_or_404(id)
    
    if request.method == 'POST':
        plan.name = request.form['name']
        plan.description = request.form.get('description')
        plan.min_ram_gb = request.form.get('min_ram_gb') or None
        plan.min_vram_gb = request.form.get('min_vram_gb') or None
        plan.cpu_socket = request.form.get('cpu_socket') or None
        plan.use_case = request.form.get('use_case') or None
        plan.budget = request.form.get('budget') or None
        plan.status = request.form.get('status', 'Planning')
        
        db.session.commit()
        flash('Build plan updated!', 'success')
        return redirect(url_for('planner.detail', id=plan.id))
    
    sockets = db.session.query(HardwareSpec.cpu_socket).filter(
        HardwareSpec.cpu_socket.isnot(None)
    ).distinct().all()
    sockets = [s[0] for s in sockets if s[0]]
    
    return render_template('planner/edit.html', plan=plan, sockets=sockets)


@bp.route('/<int:id>/delete', methods=['POST'])
def delete_plan(id):
    """Delete build plan."""
    plan = BuildPlan.query.get_or_404(id)
    db.session.delete(plan)
    db.session.commit()
    flash('Build plan deleted.', 'info')
    return redirect(url_for('planner.index'))


@bp.route('/<int:id>/add-component', methods=['POST'])
def add_component(id):
    """Add component to build plan."""
    plan = BuildPlan.query.get_or_404(id)
    
    component = BuildPlanComponent(
        build_plan_id=id,
        inventory_id=request.form.get('inventory_id') or None,
        component_type_id=request.form['component_type_id'],
        quantity=int(request.form.get('quantity', 1)),
        notes=request.form.get('notes')
    )
    db.session.add(component)
    db.session.commit()
    
    flash('Component added to build!', 'success')
    return redirect(url_for('planner.detail', id=id))


@bp.route('/<int:id>/remove-component/<int:comp_id>', methods=['POST'])
def remove_component(id, comp_id):
    """Remove component from build plan."""
    component = BuildPlanComponent.query.get_or_404(comp_id)
    db.session.delete(component)
    db.session.commit()
    flash('Component removed.', 'info')
    return redirect(url_for('planner.detail', id=id))


@bp.route('/<int:id>/check')
def check_compatibility(id):
    """API endpoint for compatibility check."""
    plan = BuildPlan.query.get_or_404(id)
    return jsonify(check_availability(plan))


def check_availability(plan):
    """
    Check what parts are available for the build requirements.
    Returns analysis of available inventory vs requirements.
    """
    results = {
        'ram': {'status': 'unknown', 'available': 0, 'required': plan.min_ram_gb, 'items': []},
        'gpu': {'status': 'unknown', 'available': [], 'required': plan.min_vram_gb, 'items': []},
        'cpu': {'status': 'unknown', 'available': [], 'socket': plan.cpu_socket, 'items': []},
        'cooler': {'status': 'unknown', 'available': [], 'socket': plan.cpu_socket, 'items': []},
        'motherboard': {'status': 'unknown', 'available': [], 'socket': plan.cpu_socket, 'items': []},
        'psu': {'status': 'unknown', 'available': [], 'items': []},
        'case': {'status': 'unknown', 'available': [], 'items': []},
        'storage': {'status': 'unknown', 'available': [], 'items': []},
    }
    
    # Get component type IDs
    type_map = {ct.name.lower(): ct.id for ct in ComponentType.query.all()}
    
    # Check RAM
    if 'ram' in type_map:
        ram_items = Inventory.query.filter_by(
            component_type_id=type_map['ram'],
            status='Available'
        ).all()
        
        total_ram = 0
        for item in ram_items:
            # Try to get capacity from spec or custom name
            capacity = 0
            if item.hardware_spec and item.hardware_spec.ram_size:
                capacity = item.hardware_spec.ram_size * item.quantity
            total_ram += capacity
            results['ram']['items'].append({
                'id': item.id,
                'name': item.display_name,
                'quantity': item.quantity,
                'capacity': capacity
            })
        
        results['ram']['available'] = total_ram
        if plan.min_ram_gb:
            results['ram']['status'] = 'ok' if total_ram >= plan.min_ram_gb else 'insufficient'
    
    # Check GPUs
    if 'gpu' in type_map:
        gpu_items = Inventory.query.filter_by(
            component_type_id=type_map['gpu'],
            status='Available'
        ).all()
        
        for item in gpu_items:
            vram = 0
            if item.hardware_spec and item.hardware_spec.gpu_memory_size:
                vram = item.hardware_spec.gpu_memory_size // 1024  # Convert MB to GB
            
            if not plan.min_vram_gb or vram >= plan.min_vram_gb:
                results['gpu']['items'].append({
                    'id': item.id,
                    'name': item.display_name,
                    'quantity': item.quantity,
                    'vram_gb': vram
                })
        
        results['gpu']['status'] = 'ok' if results['gpu']['items'] else 'none'
        if plan.min_vram_gb and not results['gpu']['items']:
            results['gpu']['status'] = 'insufficient'
    
    # Check CPUs (filter by socket if specified)
    if 'cpu' in type_map:
        cpu_query = Inventory.query.filter_by(
            component_type_id=type_map['cpu'],
            status='Available'
        )
        cpu_items = cpu_query.all()
        
        for item in cpu_items:
            socket = None
            if item.hardware_spec:
                socket = item.hardware_spec.cpu_socket
            
            # Filter by socket if specified
            if not plan.cpu_socket or (socket and plan.cpu_socket.lower() in socket.lower()):
                results['cpu']['items'].append({
                    'id': item.id,
                    'name': item.display_name,
                    'quantity': item.quantity,
                    'socket': socket,
                    'cores': item.hardware_spec.cpu_cores if item.hardware_spec else None,
                    'threads': item.hardware_spec.cpu_threads if item.hardware_spec else None
                })
        
        results['cpu']['status'] = 'ok' if results['cpu']['items'] else 'none'
    
    # Check Coolers (compatible with socket)
    if 'cooler' in type_map:
        cooler_items = Inventory.query.filter_by(
            component_type_id=type_map['cooler'],
            status='Available'
        ).all()
        
        for item in cooler_items:
            # For coolers, we'd check socket compatibility
            # This would need cooler socket support data
            results['cooler']['items'].append({
                'id': item.id,
                'name': item.display_name,
                'quantity': item.quantity
            })
        
        results['cooler']['status'] = 'ok' if results['cooler']['items'] else 'none'
    
    # Check Motherboards (filter by socket)
    if 'motherboard' in type_map:
        mb_items = Inventory.query.filter_by(
            component_type_id=type_map['motherboard'],
            status='Available'
        ).all()
        
        for item in mb_items:
            socket = None
            if item.hardware_spec:
                socket = item.hardware_spec.cpu_socket
            
            if not plan.cpu_socket or (socket and plan.cpu_socket.lower() in socket.lower()):
                results['motherboard']['items'].append({
                    'id': item.id,
                    'name': item.display_name,
                    'quantity': item.quantity,
                    'socket': socket
                })
        
        results['motherboard']['status'] = 'ok' if results['motherboard']['items'] else 'none'
    
    # Check other components (just list available)
    for comp_type in ['psu', 'case', 'storage']:
        if comp_type in type_map:
            items = Inventory.query.filter_by(
                component_type_id=type_map[comp_type],
                status='Available'
            ).all()
            
            for item in items:
                results[comp_type]['items'].append({
                    'id': item.id,
                    'name': item.display_name,
                    'quantity': item.quantity
                })
            
            results[comp_type]['status'] = 'ok' if results[comp_type]['items'] else 'none'
    
    return results
