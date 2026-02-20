import os
from flask import Blueprint, render_template
from app.models import HardwareSpec, ComponentType

bp = Blueprint('scraper', __name__)


@bp.route('/')
def index():
    """Spec lookup info page."""
    # Count specs by type
    cpu_type = ComponentType.query.filter_by(name='CPU').first()
    gpu_type = ComponentType.query.filter_by(name='GPU').first()
    mobo_type = ComponentType.query.filter_by(name='Motherboard').first()
    psu_type = ComponentType.query.filter_by(name='PSU').first()
    
    total_specs = HardwareSpec.query.count()
    cpu_count = HardwareSpec.query.filter_by(component_type_id=cpu_type.id).count() if cpu_type else 0
    gpu_count = HardwareSpec.query.filter_by(component_type_id=gpu_type.id).count() if gpu_type else 0
    mobo_count = HardwareSpec.query.filter_by(component_type_id=mobo_type.id).count() if mobo_type else 0
    psu_count = HardwareSpec.query.filter_by(component_type_id=psu_type.id).count() if psu_type else 0
    
    # Check if Scrape.Do is configured
    scrapedo_configured = bool(os.environ.get('SCRAPEDO_TOKEN'))
    
    return render_template('scraper/index.html',
        total_specs=total_specs,
        cpu_count=cpu_count,
        gpu_count=gpu_count,
        mobo_count=mobo_count,
        psu_count=psu_count,
        scrapedo_configured=scrapedo_configured
    )
