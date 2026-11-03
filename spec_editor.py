"""Field definitions and coercion for the manual HardwareSpec editor."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

COMMON_FIELDS = [
    ('manufacturer', 'Manufacturer', 'text', None),
    ('model', 'Model', 'text', None),
    ('release_date', 'Release Date', 'date', None),
    ('msrp', 'MSRP', 'decimal', 'USD'),
]

FIELDS_BY_TYPE = {
    'CPU': [
        ('cpu_socket', 'Socket', 'text', None), ('cpu_cores', 'Cores', 'int', None),
        ('cpu_threads', 'Threads', 'int', None), ('cpu_base_clock', 'Base Clock', 'decimal', 'GHz'),
        ('cpu_boost_clock', 'Boost Clock', 'decimal', 'GHz'), ('cpu_tdp', 'TDP', 'int', 'W'),
        ('cpu_architecture', 'Architecture', 'text', None),
    ],
    'GPU': [
        ('gpu_memory_size', 'VRAM', 'int', 'GB'), ('gpu_memory_type', 'Memory Type', 'text', None),
        ('gpu_base_clock', 'Base Clock', 'int', 'MHz'), ('gpu_boost_clock', 'Boost Clock', 'int', 'MHz'),
        ('gpu_tdp', 'TDP', 'int', 'W'), ('gpu_bus_interface', 'Bus Interface', 'text', None),
    ],
    'RAM': [
        ('ram_size', 'Total Capacity', 'int', 'GB'), ('ram_type', 'Memory Type', 'text', None),
        ('ram_speed', 'Speed', 'int', 'MHz'), ('ram_cas_latency', 'CAS Latency', 'text', None),
        ('ram_modules', 'Modules / Sticks', 'int', None), ('ram_ecc', 'ECC', 'bool', None),
        ('ram_module_type', 'Module Type', 'text', None),
    ],
    'Motherboard': [
        ('mobo_socket', 'Socket', 'text', None), ('mobo_chipset', 'Chipset', 'text', None),
        ('mobo_form_factor', 'Form Factor', 'text', None), ('mobo_memory_slots', 'Memory Slots', 'int', None),
        ('mobo_memory_type', 'Memory Type', 'text', None), ('mobo_max_memory', 'Max Memory', 'int', 'GB'),
        ('mobo_pcie_x16_slots', 'PCIe x16 Slots', 'int', None), ('mobo_pcie_x4_slots', 'PCIe x4 Slots', 'int', None),
        ('mobo_pcie_x1_slots', 'PCIe x1 Slots', 'int', None), ('mobo_m2_slots', 'M.2 Slots', 'int', None),
        ('mobo_sata_ports', 'SATA Ports', 'int', None),
    ],
    'Storage': [
        ('storage_capacity', 'Capacity', 'int', 'GB'), ('storage_interface', 'Interface', 'text', None),
        ('storage_type', 'Storage Type', 'text', None), ('storage_form_factor', 'Form Factor', 'text', None),
        ('storage_read_speed', 'Read Speed', 'int', 'MB/s'), ('storage_write_speed', 'Write Speed', 'int', 'MB/s'),
    ],
    'PSU': [
        ('psu_wattage', 'Wattage', 'int', 'W'), ('psu_efficiency', 'Efficiency', 'text', None),
        ('psu_modular', 'Modular', 'text', None), ('psu_form_factor', 'Form Factor', 'text', None),
    ],
    'Cooler': [
        ('cooler_type', 'Cooler Type', 'text', None), ('cooler_socket_support', 'Socket Support', 'text', None),
        ('cooler_tdp_rating', 'TDP Rating', 'int', 'W'), ('cooler_fan_size', 'Fan Size', 'int', 'mm'),
        ('cooler_height', 'Height', 'int', 'mm'),
    ],
    'Case': [
        ('case_form_factor', 'Supported Form Factor', 'text', None), ('case_type', 'Case Type', 'text', None),
        ('case_max_gpu_length', 'Max GPU Length', 'int', 'mm'), ('case_max_cooler_height', 'Max Cooler Height', 'int', 'mm'),
    ],
    'Fan': [
        ('fan_size', 'Fan Size', 'int', 'mm'), ('fan_rpm_max', 'Max RPM', 'int', 'RPM'),
        ('fan_airflow', 'Airflow', 'decimal', 'CFM'), ('fan_noise', 'Noise', 'decimal', 'dBA'),
        ('fan_connector', 'Connector', 'text', None),
    ],
    'NIC': [
        ('nic_speed', 'Speed', 'text', None), ('nic_interface', 'Interface', 'text', None),
        ('nic_ports', 'Ports', 'int', None),
    ],
    'Sound Card': [
        ('sound_interface', 'Interface', 'text', None), ('sound_channels', 'Channels', 'decimal', None),
        ('sound_sample_rate', 'Sample Rate', 'int', 'Hz'),
    ],
}


def editable_fields(component_type_name):
    return COMMON_FIELDS + FIELDS_BY_TYPE.get(component_type_name or '', [])


def type_specific_field_names(component_type_name=None):
    """Return type-specific HardwareSpec field names.

    With a component type, returns only fields for that type. Without one,
    returns every known type-specific field.
    """
    if component_type_name is not None:
        return {field[0] for field in FIELDS_BY_TYPE.get(component_type_name or '', [])}
    return {field[0] for fields in FIELDS_BY_TYPE.values() for field in fields}


def clear_irrelevant_type_fields(spec, target_component_type_name):
    """Clear stale fields that belong to component types other than target.

    Returns the list of fields whose values were cleared. Common fields such as
    manufacturer/model are never touched.
    """
    keep = type_specific_field_names(target_component_type_name)
    changed = []
    for field_name in sorted(type_specific_field_names() - keep):
        if getattr(spec, field_name, None) is not None:
            setattr(spec, field_name, None)
            changed.append(field_name)
    return changed


def coerce_value(raw, field_type):
    """Convert HTML form text to model-compatible values. Blank means NULL."""
    if raw is None or str(raw).strip() == '':
        return None
    text = str(raw).strip()
    if field_type == 'text':
        return text
    if field_type == 'int':
        return int(text)
    if field_type == 'decimal':
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f'Invalid number: {text}') from exc
    if field_type == 'date':
        return datetime.strptime(text, '%Y-%m-%d').date()
    if field_type == 'bool':
        if text.lower() in {'true', '1', 'yes', 'on'}:
            return True
        if text.lower() in {'false', '0', 'no', 'off'}:
            return False
        if text.lower() in {'null', 'unknown', 'none'}:
            return None
        raise ValueError(f'Invalid boolean value: {text}')
    return text
