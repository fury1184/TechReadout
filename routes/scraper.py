import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import HardwareSpec, ComponentType, AppSetting

bp = Blueprint('scraper', __name__)


@bp.route('/')
def index():
    """Spec lookup info page."""
    cpu_type  = ComponentType.query.filter_by(name='CPU').first()
    gpu_type  = ComponentType.query.filter_by(name='GPU').first()
    mobo_type = ComponentType.query.filter_by(name='Motherboard').first()
    psu_type  = ComponentType.query.filter_by(name='PSU').first()

    total_specs = HardwareSpec.query.count()
    cpu_count   = HardwareSpec.query.filter_by(component_type_id=cpu_type.id).count()  if cpu_type  else 0
    gpu_count   = HardwareSpec.query.filter_by(component_type_id=gpu_type.id).count()  if gpu_type  else 0
    mobo_count  = HardwareSpec.query.filter_by(component_type_id=mobo_type.id).count() if mobo_type else 0
    psu_count   = HardwareSpec.query.filter_by(component_type_id=psu_type.id).count()  if psu_type  else 0

    scrapedo_token_present = bool(os.environ.get('SCRAPEDO_TOKEN'))
    scrapedo_enabled       = AppSetting.get_bool('enable_scrapedo_fallback', True)
    scrapedo_lookup_depth  = AppSetting.get('scrapedo_lookup_depth', 'normal')
    seed_version           = AppSetting.get('seed_version', default='not imported')

    from app.scrapers.openwebui import openwebui_enabled as _openwebui_ready
    openwebui_active = _openwebui_ready()

    return render_template('scraper/index.html',
        total_specs=total_specs,
        cpu_count=cpu_count,
        gpu_count=gpu_count,
        mobo_count=mobo_count,
        psu_count=psu_count,
        scrapedo_configured=scrapedo_token_present,
        scrapedo_enabled=scrapedo_enabled,
        scrapedo_token_present=scrapedo_token_present,
        scrapedo_lookup_depth=scrapedo_lookup_depth,
        seed_version=seed_version,
        openwebui_active=openwebui_active,
    )


@bp.route('/settings', methods=['GET', 'POST'])
def settings():
    """Spec lookup settings page."""
    if request.method == 'POST':
        AppSetting.set('enable_scrapedo_fallback',
                       'true' if request.form.get('enable_scrapedo_fallback') else 'false')
        AppSetting.set('scrapedo_lookup_depth',
                       request.form.get('scrapedo_lookup_depth', 'normal'))
        AppSetting.set('ebay_pricing_enabled',
                       'true' if request.form.get('ebay_pricing_enabled') else 'false')
        AppSetting.set('openwebui_enabled',
                       'true' if request.form.get('openwebui_enabled') else 'false')
        AppSetting.set('openwebui_api_url', request.form.get('openwebui_api_url', '').strip())
        AppSetting.set('openwebui_model', request.form.get('openwebui_model', '').strip())
        db.session.commit()
        flash('Spec lookup settings saved.', 'success')
        return redirect(url_for('scraper.settings'))

    return render_template(
        'scraper/settings.html',
        scrapedo_token_present  = bool(os.environ.get('SCRAPEDO_TOKEN')),
        enable_scrapedo_fallback= AppSetting.get_bool('enable_scrapedo_fallback', True),
        scrapedo_lookup_depth   = AppSetting.get('scrapedo_lookup_depth', 'normal'),
        ebay_pricing_enabled    = AppSetting.get_bool('ebay_pricing_enabled', False),
        ebay_app_id_present     = bool(os.environ.get('EBAY_APP_ID')),
        ebay_app_secret_present = bool(os.environ.get('EBAY_APP_SECRET')),
        openwebui_enabled          = AppSetting.get_bool('openwebui_enabled', False),
        openwebui_api_url          = AppSetting.get('openwebui_api_url', '') or '',
        openwebui_model            = AppSetting.get('openwebui_model', '') or '',
        openwebui_token_present    = bool(os.environ.get('OPENWEBUI_API_TOKEN')),
    )
