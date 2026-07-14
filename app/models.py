from datetime import datetime, timedelta
import re
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
    ram_modules = db.Column(db.Integer)  # Number of sticks in kit
    
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

    @property
    def lookup_metadata(self):
        from app.serializers.hardware import _lookup_metadata
        return _lookup_metadata(self)

    @property
    def source_name(self):
        from app.serializers.hardware import source_name
        return source_name(self)

    @property
    def confidence(self):
        from app.serializers.hardware import confidence
        return confidence(self)

    @property
    def spec_summary(self):
        from app.serializers.hardware import spec_summary
        return spec_summary(self)

    @property
    def detail_rows(self):
        from app.serializers.hardware import detail_rows
        return detail_rows(self)


class AppSetting(db.Model):
    __tablename__ = 'app_settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get(key, default=None):
        setting = db.session.query(AppSetting).filter_by(key=key).first()
        return setting.value if setting else default

    @staticmethod
    def get_bool(key, default=False):
        value = AppSetting.get(key)
        if value is None:
            return default
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    @staticmethod
    def set(key, value):
        setting = db.session.query(AppSetting).filter_by(key=key).first()
        if not setting:
            setting = AppSetting(key=key, value=str(value))
            db.session.add(setting)
        else:
            setting.value = str(value)
        return setting


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
    status = db.Column(db.Enum('Unverified', 'Verified', 'Installed', 'Missing', 'Sold', 'Dead'), default='Unverified')
    assigned_to_host_id = db.Column(db.Integer, db.ForeignKey('hosts.id'))
    
    # Price metadata
    price_is_estimate = db.Column(db.Boolean, default=False, nullable=False)

    # Sale tracking
    sale_date = db.Column(db.Date)
    sale_price = db.Column(db.Numeric(10, 2))
    sold_to = db.Column(db.String(200))  # buyer name/platform
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def display_name(self):
        # Custom name takes priority (user explicitly set it)
        if self.custom_name:
            if self.custom_manufacturer:
                return f"{self.custom_manufacturer} {self.custom_name}"
            return self.custom_name
        # Fall back to hardware spec name
        if self.hardware_spec:
            return self.hardware_spec.display_name
        # Last resort
        if self.custom_manufacturer:
            return f"{self.custom_manufacturer} (Unknown Model)"
        return "Unknown"
    
    @property
    def profit(self):
        """Calculate profit/loss if sold (purchase_price is per unit)."""
        if self.sale_price is not None and self.purchase_price is not None:
            return float(self.sale_price) - float(self.purchase_price) * (self.quantity or 1)
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


class LookupCache(db.Model):
    __tablename__ = 'lookup_cache'

    id = db.Column(db.Integer, primary_key=True)
    cache_key = db.Column(db.String(255), unique=True, nullable=False, index=True)
    query = db.Column(db.String(255), nullable=False)
    component_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='hit')
    spec_id = db.Column(db.Integer, db.ForeignKey('hardware_specs.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    spec = db.relationship('HardwareSpec', lazy='joined')

    @staticmethod
    def normalize_query(query):
        return re.sub(r'\s+', ' ', (query or '').strip().lower())

    @staticmethod
    def make_key(query, component_type, lite_mode=False, use_intel_ark=False, use_amd_official=False):
        parts = [
            LookupCache.normalize_query(query),
            (component_type or 'auto').strip().lower(),
            'lite' if lite_mode else 'full',
            'intelark' if use_intel_ark else 'nointelark',
            'amdofficial' if use_amd_official else 'noamdofficial',
        ]
        return '|'.join(parts)

    @staticmethod
    def get_fresh(cache_key, max_age_days=30):
        if not cache_key:
            return None
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        return db.session.query(LookupCache).filter(
            LookupCache.cache_key == cache_key,
            LookupCache.updated_at >= cutoff
        ).first()

    @staticmethod
    def store_hit(cache_key, query, component_type, spec_id):
        entry = db.session.query(LookupCache).filter_by(cache_key=cache_key).first()
        if not entry:
            entry = LookupCache(cache_key=cache_key, query=query, component_type=component_type)
            db.session.add(entry)
        entry.query = query
        entry.component_type = component_type
        entry.status = 'hit'
        entry.spec_id = spec_id
        return entry

    @staticmethod
    def store_miss(cache_key, query, component_type):
        entry = db.session.query(LookupCache).filter_by(cache_key=cache_key).first()
        if not entry:
            entry = LookupCache(cache_key=cache_key, query=query, component_type=component_type)
            db.session.add(entry)
        entry.query = query
        entry.component_type = component_type
        entry.status = 'miss'
        entry.spec_id = None
        return entry


class PendingReview(db.Model):
    """Stores scrape matches that fell below the auto-accept confidence threshold."""
    __tablename__ = 'pending_reviews'

    id = db.Column(db.Integer, primary_key=True)
    query = db.Column(db.String(255), nullable=False)
    component_type = db.Column(db.String(50), nullable=False)
    candidates = db.Column(db.JSON)          # [{name, spec_id, confidence, source}, ...]
    top_confidence = db.Column(db.Float)
    status = db.Column(db.Enum('Pending', 'Accepted', 'Skipped'), default='Pending')
    resolved_spec_id = db.Column(db.Integer, db.ForeignKey('hardware_specs.id'), nullable=True)
    triggered_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    resolved_spec = db.relationship('HardwareSpec', foreign_keys=[resolved_spec_id])


class PriceCache(db.Model):
    """Caches eBay price estimates to avoid hammering the API on every page load."""
    __tablename__ = 'price_cache'

    id = db.Column(db.Integer, primary_key=True)
    search_query = db.Column(db.String(300), nullable=False, index=True)
    ebay_price = db.Column(db.Numeric(10, 2), nullable=False)
    listing_count = db.Column(db.Integer)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    TTL_HOURS = 24

    @staticmethod
    def get_fresh(search_query: str):
        """Return a cache entry younger than TTL_HOURS, or None."""
        cutoff = datetime.utcnow() - timedelta(hours=PriceCache.TTL_HOURS)
        return db.session.query(PriceCache).filter(
            PriceCache.search_query == search_query,
            PriceCache.fetched_at >= cutoff,
        ).first()
