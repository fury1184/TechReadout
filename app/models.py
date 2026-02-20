from datetime import datetime
from app import db


class ComponentType(db.Model):
    __tablename__ = 'component_types'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    specs = db.relationship('HardwareSpec', backref='component_type', lazy='dynamic')
    inventory_items = db.relationship('Inventory', backref='component_type', lazy='dynamic')


class HardwareSpec(db.Model):
    __tablename__ = 'hardware_specs'
    
    id = db.Column(db.Integer, primary_key=True)
    component_type_id = db.Column(db.Integer, db.ForeignKey('component_types.id'), nullable=False)
    manufacturer = db.Column(db.String(100))
    model = db.Column(db.String(200), nullable=False)
    
    # Common specs
    release_date = db.Column(db.Date)
    msrp = db.Column(db.Numeric(10, 2))
    
    # CPU-specific
    cpu_socket = db.Column(db.String(50))
    cpu_cores = db.Column(db.Integer)
    cpu_threads = db.Column(db.Integer)
    cpu_base_clock = db.Column(db.Numeric(5, 2))
    cpu_boost_clock = db.Column(db.Numeric(5, 2))
    cpu_tdp = db.Column(db.Integer)
    cpu_architecture = db.Column(db.String(100))
    
    # GPU-specific
    gpu_memory_size = db.Column(db.Integer)
    gpu_memory_type = db.Column(db.String(20))
    gpu_base_clock = db.Column(db.Integer)
    gpu_boost_clock = db.Column(db.Integer)
    gpu_tdp = db.Column(db.Integer)
    gpu_bus_interface = db.Column(db.String(20))
    
    # RAM-specific
    ram_size = db.Column(db.Integer)
    ram_type = db.Column(db.String(20))
    ram_speed = db.Column(db.Integer)
    ram_cas_latency = db.Column(db.String(20))
    
    # Motherboard-specific
    mobo_socket = db.Column(db.String(50))
    mobo_chipset = db.Column(db.String(50))
    mobo_form_factor = db.Column(db.String(20))
    mobo_memory_slots = db.Column(db.Integer)
    mobo_memory_type = db.Column(db.String(20))
    mobo_max_memory = db.Column(db.Integer)
    mobo_pcie_x16_slots = db.Column(db.Integer)
    mobo_pcie_x4_slots = db.Column(db.Integer)
    mobo_pcie_x1_slots = db.Column(db.Integer)
    mobo_m2_slots = db.Column(db.Integer)
    mobo_sata_ports = db.Column(db.Integer)
    
    # Storage-specific
    storage_capacity = db.Column(db.Integer)
    storage_interface = db.Column(db.String(50))
    storage_type = db.Column(db.String(20))
    storage_form_factor = db.Column(db.String(20))
    storage_read_speed = db.Column(db.Integer)
    storage_write_speed = db.Column(db.Integer)
    
    # PSU-specific
    psu_wattage = db.Column(db.Integer)
    psu_efficiency = db.Column(db.String(20))
    psu_modular = db.Column(db.String(20))
    psu_form_factor = db.Column(db.String(20))
    
    # Cooler-specific
    cooler_type = db.Column(db.String(50))  # e.g., "AIO 360mm", "Air"
    cooler_socket_support = db.Column(db.String(200))
    cooler_tdp_rating = db.Column(db.Integer)
    cooler_fan_size = db.Column(db.Integer)
    cooler_height = db.Column(db.Integer)
    
    # Case-specific
    case_form_factor = db.Column(db.String(50))
    case_type = db.Column(db.String(50))
    case_max_gpu_length = db.Column(db.Integer)
    case_max_cooler_height = db.Column(db.Integer)
    
    # Fan-specific
    fan_size = db.Column(db.Integer)
    fan_rpm_max = db.Column(db.Integer)
    fan_airflow = db.Column(db.Numeric(5, 1))
    fan_noise = db.Column(db.Numeric(4, 1))
    fan_connector = db.Column(db.String(20))
    
    # NIC-specific
    nic_speed = db.Column(db.String(20))
    nic_interface = db.Column(db.String(20))
    nic_ports = db.Column(db.Integer)
    
    # Sound Card-specific
    sound_interface = db.Column(db.String(20))
    sound_channels = db.Column(db.Numeric(3, 1))
    sound_sample_rate = db.Column(db.Integer)
    
    # Metadata
    source_url = db.Column(db.String(500))
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    raw_data = db.Column(db.JSON)
    
    inventory_items = db.relationship('Inventory', backref='hardware_spec', lazy='dynamic')
    
    @property
    def display_name(self):
        if self.manufacturer:
            return f"{self.manufacturer} {self.model}"
        return self.model


class Inventory(db.Model):
    __tablename__ = 'inventory'
    
    id = db.Column(db.Integer, primary_key=True)
    hardware_spec_id = db.Column(db.Integer, db.ForeignKey('hardware_specs.id'))
    component_type_id = db.Column(db.Integer, db.ForeignKey('component_types.id'), nullable=False)
    
    # For items not in specs database
    custom_name = db.Column(db.String(200))
    custom_manufacturer = db.Column(db.String(100))
    
    # Inventory details
    quantity = db.Column(db.Integer, default=1)
    purchase_date = db.Column(db.Date)
    purchase_price = db.Column(db.Numeric(10, 2))
    item_condition = db.Column(db.Enum('New', 'Used', 'Refurbished', 'For Parts'), default='New')
    location = db.Column(db.String(100))
    notes = db.Column(db.Text)
    
    # Status
    status = db.Column(db.Enum('Available', 'In Use', 'Reserved', 'Sold', 'Disposed'), default='Available')
    assigned_to_host_id = db.Column(db.Integer, db.ForeignKey('hosts.id'))
    
    # Sale tracking
    sale_date = db.Column(db.Date)
    sale_price = db.Column(db.Numeric(10, 2))
    sold_to = db.Column(db.String(200))  # buyer name/platform
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def display_name(self):
        if self.hardware_spec:
            return self.hardware_spec.display_name
        if self.custom_manufacturer:
            return f"{self.custom_manufacturer} {self.custom_name}"
        return self.custom_name or "Unknown"
    
    @property
    def profit(self):
        """Calculate profit/loss if sold."""
        if self.sale_price is not None and self.purchase_price is not None:
            return float(self.sale_price) - float(self.purchase_price)
        return None


class Host(db.Model):
    __tablename__ = 'hosts'
    
    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    purpose = db.Column(db.String(100))
    os = db.Column(db.String(100))
    ip_address = db.Column(db.String(45))
    mac_address = db.Column(db.String(17))
    
    status = db.Column(db.Enum('Active', 'Inactive', 'Planned', 'Decommissioned'), default='Active')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    components = db.relationship('Inventory', backref='assigned_host', lazy='dynamic')


class BuildPlan(db.Model):
    __tablename__ = 'build_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.Enum('Planning', 'Ready', 'Building', 'Complete', 'Cancelled'), default='Planning')
    
    # Requirements
    min_ram_gb = db.Column(db.Integer)
    min_vram_gb = db.Column(db.Integer)
    cpu_socket = db.Column(db.String(50))
    use_case = db.Column(db.String(100))  # Gaming, Workstation, Server, HTPC
    
    # Budget
    budget = db.Column(db.Numeric(10, 2))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Components assigned to this build
    components = db.relationship('BuildPlanComponent', backref='build_plan', lazy='dynamic', cascade='all, delete-orphan')


class BuildPlanComponent(db.Model):
    __tablename__ = 'build_plan_components'
    
    id = db.Column(db.Integer, primary_key=True)
    build_plan_id = db.Column(db.Integer, db.ForeignKey('build_plans.id'), nullable=False)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'))
    component_type_id = db.Column(db.Integer, db.ForeignKey('component_types.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    notes = db.Column(db.Text)


class Backup(db.Model):
    __tablename__ = 'backups'
    
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    backup_type = db.Column(db.Enum('Local', 'NAS'), default='Local')
    size_bytes = db.Column(db.BigInteger)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)


class ScrapeJob(db.Model):
    __tablename__ = 'scrape_jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), nullable=False)
    component_type = db.Column(db.String(50))
    status = db.Column(db.Enum('Pending', 'Running', 'Completed', 'Failed'), default='Pending')
    items_found = db.Column(db.Integer, default=0)
    items_added = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
