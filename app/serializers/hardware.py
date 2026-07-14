"""Shared serializers and display helpers for TechReadOut hardware specs.

This module is intentionally model-agnostic: pass in a HardwareSpec-like object
and it returns JSON/display-ready data. Keeping this logic here prevents the
API, templates, exports, review queue, and future compatibility checker from
building slightly different versions of the same spec.
"""
from datetime import date, datetime
from decimal import Decimal


CPU_FIELDS = (
    'cpu_socket', 'cpu_cores', 'cpu_threads', 'cpu_base_clock',
    'cpu_boost_clock', 'cpu_tdp', 'cpu_architecture',
)
GPU_FIELDS = (
    'gpu_memory_size', 'gpu_memory_type', 'gpu_base_clock',
    'gpu_boost_clock', 'gpu_tdp', 'gpu_bus_interface',
)
MOTHERBOARD_FIELDS = (
    'mobo_socket', 'mobo_chipset', 'mobo_form_factor',
    'mobo_memory_slots', 'mobo_memory_type', 'mobo_max_memory',
    'mobo_pcie_x16_slots', 'mobo_pcie_x4_slots', 'mobo_pcie_x1_slots',
    'mobo_m2_slots', 'mobo_sata_ports',
)
RAM_FIELDS = ('ram_size', 'ram_type', 'ram_speed', 'ram_cas_latency', 'ram_modules')
STORAGE_FIELDS = (
    'storage_capacity', 'storage_interface', 'storage_type',
    'storage_form_factor', 'storage_read_speed', 'storage_write_speed',
)
PSU_FIELDS = ('psu_wattage', 'psu_efficiency', 'psu_modular', 'psu_form_factor')
COOLER_FIELDS = (
    'cooler_type', 'cooler_socket_support', 'cooler_tdp_rating',
    'cooler_fan_size', 'cooler_height',
)
CASE_FIELDS = ('case_form_factor', 'case_type', 'case_max_gpu_length', 'case_max_cooler_height')
FAN_FIELDS = ('fan_size', 'fan_rpm_max', 'fan_airflow', 'fan_noise', 'fan_connector')
NIC_FIELDS = ('nic_speed', 'nic_interface', 'nic_ports')
SOUND_FIELDS = ('sound_interface', 'sound_channels', 'sound_sample_rate')

SPEC_FIELDS = (
    CPU_FIELDS + GPU_FIELDS + MOTHERBOARD_FIELDS + RAM_FIELDS + STORAGE_FIELDS +
    PSU_FIELDS + COOLER_FIELDS + CASE_FIELDS + FAN_FIELDS + NIC_FIELDS + SOUND_FIELDS
)


def _component_type_name(spec):
    ct = getattr(spec, 'component_type', None)
    return getattr(ct, 'name', None) or getattr(spec, 'component_type_name', None) or ''


def _display_name(spec):
    manufacturer = getattr(spec, 'manufacturer', None)
    model = getattr(spec, 'model', None)
    return f"{manufacturer} {model}" if manufacturer else model


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _lookup_metadata(spec):
    raw_data = getattr(spec, 'raw_data', None)
    if isinstance(raw_data, dict):
        meta = raw_data.get('_lookup_metadata')
        return meta if isinstance(meta, dict) else {}
    return {}


def source_name(spec, fallback='Database'):
    meta = _lookup_metadata(spec)
    raw_data = getattr(spec, 'raw_data', None)
    if meta.get('source_name'):
        return meta.get('source_name')
    if isinstance(raw_data, dict) and raw_data.get('source'):
        return raw_data.get('source')
    if getattr(spec, 'source_url', None):
        return 'Web scrape'
    return fallback


def confidence(spec):
    meta = _lookup_metadata(spec)
    value = meta.get('confidence') or meta.get('final_confidence')
    try:
        return int(round(float(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


def _row(label, value, unit=None):
    if value is None or value == '':
        return None
    if unit:
        return (label, f"{value}{unit}")
    return (label, value)


def spec_summary(spec):
    """Compact component-specific summary for tables/cards."""
    ct = _component_type_name(spec)
    parts = []

    if ct == 'CPU':
        cores = getattr(spec, 'cpu_cores', None)
        threads = getattr(spec, 'cpu_threads', None)
        if cores or threads:
            parts.append(f"{cores or '?'}C/{threads or cores or '?'}T")
        base = getattr(spec, 'cpu_base_clock', None)
        boost = getattr(spec, 'cpu_boost_clock', None)
        if base or boost:
            base_text = f"{float(base):.2f}" if base else '?'
            boost_text = f"{float(boost):.2f}" if boost else '?'
            parts.append(f"{base_text}/{boost_text} GHz")
        for attr in ('cpu_socket',):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        tdp = getattr(spec, 'cpu_tdp', None)
        if tdp:
            parts.append(f"{tdp}W")

    elif ct == 'GPU':
        memory = getattr(spec, 'gpu_memory_size', None)
        memory_type = getattr(spec, 'gpu_memory_type', None)
        if memory:
            vram = int(memory / 1024) if memory >= 128 else int(memory)
            parts.append(f"{vram}GB {memory_type or 'VRAM'}")
        elif memory_type:
            parts.append(memory_type)
        boost = getattr(spec, 'gpu_boost_clock', None)
        if boost:
            parts.append(f"{boost}MHz boost")
        for attr in ('gpu_bus_interface',):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        tdp = getattr(spec, 'gpu_tdp', None)
        if tdp:
            parts.append(f"{tdp}W")

    elif ct == 'Motherboard':
        for attr in ('mobo_socket', 'mobo_chipset', 'mobo_form_factor'):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        slots = getattr(spec, 'mobo_memory_slots', None)
        mem_type = getattr(spec, 'mobo_memory_type', None)
        if slots or mem_type:
            parts.append(f"{slots} slots {mem_type}" if slots and mem_type else mem_type or f"{slots} memory slots")
        m2 = getattr(spec, 'mobo_m2_slots', None)
        sata = getattr(spec, 'mobo_sata_ports', None)
        if m2:
            parts.append(f"{m2}x M.2")
        if sata:
            parts.append(f"{sata}x SATA")

    elif ct == 'RAM':
        size = getattr(spec, 'ram_size', None)
        if size:
            parts.append(f"{size}GB")
        ram_type = getattr(spec, 'ram_type', None)
        speed = getattr(spec, 'ram_speed', None)
        if ram_type or speed:
            parts.append(f"{ram_type or ''}-{speed}".strip('-'))
        latency = getattr(spec, 'ram_cas_latency', None)
        modules = getattr(spec, 'ram_modules', None)
        if latency:
            parts.append(str(latency))
        if modules:
            parts.append(f"{modules} sticks")

    elif ct == 'Storage':
        capacity = getattr(spec, 'storage_capacity', None)
        if capacity:
            cap = f"{int(capacity / 1000)}TB" if capacity >= 1000 else f"{capacity}GB"
            parts.append(cap)
        for attr in ('storage_type', 'storage_interface', 'storage_form_factor'):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        read_speed = getattr(spec, 'storage_read_speed', None)
        if read_speed:
            parts.append(f"{read_speed}MB/s read")

    elif ct == 'PSU':
        wattage = getattr(spec, 'psu_wattage', None)
        if wattage:
            parts.append(f"{wattage}W")
        for attr in ('psu_efficiency', 'psu_modular', 'psu_form_factor'):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)

    elif ct == 'Cooler':
        for attr in ('cooler_type', 'cooler_socket_support'):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        for attr, label in (('cooler_fan_size', 'mm fan'), ('cooler_height', 'mm tall'), ('cooler_tdp_rating', 'W TDP')):
            value = getattr(spec, attr, None)
            if value:
                parts.append(f"{value}{label}")

    elif ct == 'Case':
        for attr in ('case_type', 'case_form_factor'):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        gpu_len = getattr(spec, 'case_max_gpu_length', None)
        cooler_h = getattr(spec, 'case_max_cooler_height', None)
        if gpu_len:
            parts.append(f"GPU {gpu_len}mm")
        if cooler_h:
            parts.append(f"Cooler {cooler_h}mm")

    elif ct == 'Fan':
        for attr, fmt in (('fan_size', '{}mm'), ('fan_rpm_max', '{}RPM')):
            value = getattr(spec, attr, None)
            if value:
                parts.append(fmt.format(value))
        airflow = getattr(spec, 'fan_airflow', None)
        connector = getattr(spec, 'fan_connector', None)
        if airflow:
            parts.append(f"{float(airflow):.1f}CFM")
        if connector:
            parts.append(connector)

    elif ct == 'NIC':
        for attr in ('nic_speed', 'nic_interface'):
            value = getattr(spec, attr, None)
            if value:
                parts.append(value)
        ports = getattr(spec, 'nic_ports', None)
        if ports:
            parts.append(f"{ports} ports")

    elif ct == 'Sound Card':
        channels = getattr(spec, 'sound_channels', None)
        sample_rate = getattr(spec, 'sound_sample_rate', None)
        interface = getattr(spec, 'sound_interface', None)
        if channels:
            parts.append(f"{float(channels):g} channels")
        if sample_rate:
            parts.append(f"{sample_rate}Hz")
        if interface:
            parts.append(interface)

    return ' · '.join(str(p) for p in parts if p) or 'No key specs recorded'


def detail_rows(spec, as_dict=False):
    """Full component-specific details for display/API output."""
    ct = _component_type_name(spec)
    rows = []

    if ct == 'CPU':
        base = getattr(spec, 'cpu_base_clock', None)
        boost = getattr(spec, 'cpu_boost_clock', None)
        rows = [
            _row('Cores', getattr(spec, 'cpu_cores', None)),
            _row('Threads', getattr(spec, 'cpu_threads', None)),
            _row('Base Clock', f"{float(base):.2f}" if base else None, ' GHz'),
            _row('Boost Clock', f"{float(boost):.2f}" if boost else None, ' GHz'),
            _row('TDP', getattr(spec, 'cpu_tdp', None), ' W'),
            _row('Socket', getattr(spec, 'cpu_socket', None)),
            _row('Architecture', getattr(spec, 'cpu_architecture', None)),
        ]
    elif ct == 'GPU':
        memory = getattr(spec, 'gpu_memory_size', None)
        vram = f"{int(memory / 1024)} GB" if memory and memory >= 128 else (f"{memory} GB" if memory else None)
        rows = [
            _row('VRAM', vram),
            _row('VRAM Type', getattr(spec, 'gpu_memory_type', None)),
            _row('Base Clock', getattr(spec, 'gpu_base_clock', None), ' MHz'),
            _row('Boost Clock', getattr(spec, 'gpu_boost_clock', None), ' MHz'),
            _row('TDP', getattr(spec, 'gpu_tdp', None), ' W'),
            _row('Bus Interface', getattr(spec, 'gpu_bus_interface', None)),
        ]
    elif ct == 'Motherboard':
        rows = [
            _row('Socket', getattr(spec, 'mobo_socket', None)),
            _row('Chipset', getattr(spec, 'mobo_chipset', None)),
            _row('Form Factor', getattr(spec, 'mobo_form_factor', None)),
            _row('Memory Type', getattr(spec, 'mobo_memory_type', None)),
            _row('Memory Slots', getattr(spec, 'mobo_memory_slots', None)),
            _row('Max Memory', getattr(spec, 'mobo_max_memory', None), ' GB'),
            _row('PCIe x16 Slots', getattr(spec, 'mobo_pcie_x16_slots', None)),
            _row('PCIe x4 Slots', getattr(spec, 'mobo_pcie_x4_slots', None)),
            _row('PCIe x1 Slots', getattr(spec, 'mobo_pcie_x1_slots', None)),
            _row('M.2 Slots', getattr(spec, 'mobo_m2_slots', None)),
            _row('SATA Ports', getattr(spec, 'mobo_sata_ports', None)),
        ]
    elif ct == 'RAM':
        rows = [
            _row('Capacity', getattr(spec, 'ram_size', None), ' GB'),
            _row('Type', getattr(spec, 'ram_type', None)),
            _row('Speed', getattr(spec, 'ram_speed', None), ' MHz'),
            _row('CAS Latency', getattr(spec, 'ram_cas_latency', None)),
            _row('Modules', getattr(spec, 'ram_modules', None)),
        ]
    elif ct == 'Storage':
        rows = [
            _row('Capacity', getattr(spec, 'storage_capacity', None), ' GB'),
            _row('Type', getattr(spec, 'storage_type', None)),
            _row('Interface', getattr(spec, 'storage_interface', None)),
            _row('Form Factor', getattr(spec, 'storage_form_factor', None)),
            _row('Read Speed', getattr(spec, 'storage_read_speed', None), ' MB/s'),
            _row('Write Speed', getattr(spec, 'storage_write_speed', None), ' MB/s'),
        ]
    elif ct == 'PSU':
        rows = [
            _row('Wattage', getattr(spec, 'psu_wattage', None), ' W'),
            _row('Efficiency', getattr(spec, 'psu_efficiency', None)),
            _row('Modular', getattr(spec, 'psu_modular', None)),
            _row('Form Factor', getattr(spec, 'psu_form_factor', None)),
        ]
    elif ct == 'Cooler':
        rows = [
            _row('Type', getattr(spec, 'cooler_type', None)),
            _row('Fan Size', getattr(spec, 'cooler_fan_size', None), ' mm'),
            _row('Height', getattr(spec, 'cooler_height', None), ' mm'),
            _row('TDP Rating', getattr(spec, 'cooler_tdp_rating', None), ' W'),
            _row('Socket Support', getattr(spec, 'cooler_socket_support', None)),
        ]
    elif ct == 'Case':
        rows = [
            _row('Type', getattr(spec, 'case_type', None)),
            _row('Form Factor', getattr(spec, 'case_form_factor', None)),
            _row('Max GPU Length', getattr(spec, 'case_max_gpu_length', None), ' mm'),
            _row('Max Cooler Height', getattr(spec, 'case_max_cooler_height', None), ' mm'),
        ]
    elif ct == 'Fan':
        airflow = getattr(spec, 'fan_airflow', None)
        noise = getattr(spec, 'fan_noise', None)
        rows = [
            _row('Size', getattr(spec, 'fan_size', None), ' mm'),
            _row('Max RPM', getattr(spec, 'fan_rpm_max', None)),
            _row('Airflow', f"{float(airflow):.1f}" if airflow else None, ' CFM'),
            _row('Noise', f"{float(noise):.1f}" if noise else None, ' dBA'),
            _row('Connector', getattr(spec, 'fan_connector', None)),
        ]
    elif ct == 'NIC':
        rows = [
            _row('Speed', getattr(spec, 'nic_speed', None)),
            _row('Interface', getattr(spec, 'nic_interface', None)),
            _row('Ports', getattr(spec, 'nic_ports', None)),
        ]
    elif ct == 'Sound Card':
        rows = [
            _row('Channels', getattr(spec, 'sound_channels', None)),
            _row('Sample Rate', getattr(spec, 'sound_sample_rate', None), ' Hz'),
            _row('Interface', getattr(spec, 'sound_interface', None)),
        ]

    cleaned = [r for r in rows if r]
    if as_dict:
        return [{'label': label, 'value': value} for label, value in cleaned]
    return cleaned


def hardware_spec_to_dict(spec, source='database', confidence_value=100, found=True):
    """Serialize a HardwareSpec ORM object to the standard API/review dict."""
    raw_data = getattr(spec, 'raw_data', None) if isinstance(getattr(spec, 'raw_data', None), dict) else {}
    meta = _lookup_metadata(spec)
    confidence_out = confidence_value if confidence_value is not None else confidence(spec)

    data = {
        'found': found,
        'spec_id': getattr(spec, 'id', None),
        'source': source,
        'source_url': getattr(spec, 'source_url', None),
        'source_name': source_name(spec, fallback=source),
        'confidence': confidence_out,
        'lookup_metadata': meta or None,
        'spec_summary': spec_summary(spec),
        'detail_rows': detail_rows(spec, as_dict=True),
        'manufacturer': getattr(spec, 'manufacturer', None),
        'model': getattr(spec, 'model', None),
        'display_name': _display_name(spec),
        'component_type': _component_type_name(spec),
        'release_date': _json_value(getattr(spec, 'release_date', None)),
        'msrp': _float_or_none(getattr(spec, 'msrp', None)),
        'raw_data': raw_data,
    }

    for field in SPEC_FIELDS:
        data[field] = _json_value(getattr(spec, field, None))

    return data
