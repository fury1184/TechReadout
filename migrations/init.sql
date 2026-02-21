-- TechReadout Database Schema

-- Component types lookup
CREATE TABLE IF NOT EXISTS component_types (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO component_types (name) VALUES 
    ('CPU'), ('GPU'), ('RAM'), ('Motherboard'), ('Storage'), 
    ('PSU'), ('Case'), ('Cooler'), ('Fan'), ('NIC'), ('Sound Card'), ('Other');

-- Hardware specifications (scraped reference data)
CREATE TABLE IF NOT EXISTS hardware_specs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    component_type_id INT NOT NULL,
    manufacturer VARCHAR(100),
    model VARCHAR(200) NOT NULL,
    
    -- Common specs
    release_date DATE,
    msrp DECIMAL(10, 2),
    
    -- CPU-specific
    cpu_socket VARCHAR(50),
    cpu_cores INT,
    cpu_threads INT,
    cpu_base_clock DECIMAL(5, 2),
    cpu_boost_clock DECIMAL(5, 2),
    cpu_tdp INT,
    cpu_architecture VARCHAR(100),
    
    -- GPU-specific
    gpu_memory_size INT,
    gpu_memory_type VARCHAR(20),
    gpu_base_clock INT,
    gpu_boost_clock INT,
    gpu_tdp INT,
    gpu_bus_interface VARCHAR(20),
    
    -- RAM-specific
    ram_size INT,
    ram_type VARCHAR(20),
    ram_speed INT,
    ram_cas_latency VARCHAR(20),
    ram_modules INT,
    
    -- Motherboard-specific
    mobo_socket VARCHAR(50),
    mobo_chipset VARCHAR(50),
    mobo_form_factor VARCHAR(20),
    mobo_memory_slots INT,
    mobo_memory_type VARCHAR(20),
    mobo_max_memory INT,
    mobo_pcie_x16_slots INT,
    mobo_pcie_x4_slots INT,
    mobo_pcie_x1_slots INT,
    mobo_m2_slots INT,
    mobo_sata_ports INT,
    
    -- Storage-specific
    storage_capacity INT,
    storage_interface VARCHAR(50),
    storage_type VARCHAR(20),
    storage_form_factor VARCHAR(20),
    storage_read_speed INT,
    storage_write_speed INT,
    
    -- PSU-specific
    psu_wattage INT,
    psu_efficiency VARCHAR(20),
    psu_modular VARCHAR(20),
    psu_form_factor VARCHAR(20),
    
    -- Cooler-specific
    cooler_type VARCHAR(50),
    cooler_socket_support VARCHAR(200),
    cooler_tdp_rating INT,
    cooler_fan_size INT,
    cooler_height INT,
    
    -- Case-specific
    case_form_factor VARCHAR(50),
    case_type VARCHAR(50),
    case_max_gpu_length INT,
    case_max_cooler_height INT,
    
    -- Fan-specific
    fan_size INT,
    fan_rpm_max INT,
    fan_airflow DECIMAL(5,1),
    fan_noise DECIMAL(4,1),
    fan_connector VARCHAR(20),
    
    -- NIC-specific
    nic_speed VARCHAR(20),
    nic_interface VARCHAR(20),
    nic_ports INT,
    
    -- Sound Card-specific
    sound_interface VARCHAR(20),
    sound_channels DECIMAL(3,1),
    sound_sample_rate INT,
    
    -- Metadata
    source_url VARCHAR(500),
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_data JSON,
    
    FOREIGN KEY (component_type_id) REFERENCES component_types(id),
    INDEX idx_model (model),
    INDEX idx_manufacturer (manufacturer),
    INDEX idx_component_type (component_type_id)
);

-- User inventory (your actual parts)
CREATE TABLE IF NOT EXISTS inventory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    hardware_spec_id INT,
    component_type_id INT NOT NULL,
    
    -- For items not in specs database
    custom_name VARCHAR(200),
    custom_manufacturer VARCHAR(100),
    
    -- Inventory details
    quantity INT DEFAULT 1,
    purchase_date DATE,
    purchase_price DECIMAL(10, 2),
    item_condition ENUM('New', 'Used', 'Refurbished', 'For Parts') DEFAULT 'New',
    location VARCHAR(100),
    notes TEXT,
    
    -- Status
    status ENUM('Available', 'In Use', 'Reserved', 'Sold', 'Disposed') DEFAULT 'Available',
    assigned_to_host_id INT,
    
    -- Sale tracking
    sale_date DATE,
    sale_price DECIMAL(10, 2),
    sold_to VARCHAR(200),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (hardware_spec_id) REFERENCES hardware_specs(id) ON DELETE SET NULL,
    FOREIGN KEY (component_type_id) REFERENCES component_types(id)
);

-- Host systems (built machines)
CREATE TABLE IF NOT EXISTS hosts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    hostname VARCHAR(100) NOT NULL,
    description TEXT,
    purpose VARCHAR(100),
    os VARCHAR(100),
    ip_address VARCHAR(45),
    mac_address VARCHAR(17),
    
    status ENUM('Active', 'Inactive', 'Planned', 'Decommissioned') DEFAULT 'Active',
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_hostname (hostname)
);

-- Link inventory items to hosts
ALTER TABLE inventory ADD FOREIGN KEY (assigned_to_host_id) REFERENCES hosts(id) ON DELETE SET NULL;

-- Build Plans
CREATE TABLE IF NOT EXISTS build_plans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    status ENUM('Planning', 'Ready', 'Building', 'Complete', 'Cancelled') DEFAULT 'Planning',
    min_ram_gb INT,
    min_vram_gb INT,
    cpu_socket VARCHAR(50),
    use_case VARCHAR(100),
    budget DECIMAL(10, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Build Plan Components
CREATE TABLE IF NOT EXISTS build_plan_components (
    id INT AUTO_INCREMENT PRIMARY KEY,
    build_plan_id INT NOT NULL,
    inventory_id INT,
    component_type_id INT NOT NULL,
    quantity INT DEFAULT 1,
    notes TEXT,
    FOREIGN KEY (build_plan_id) REFERENCES build_plans(id) ON DELETE CASCADE,
    FOREIGN KEY (inventory_id) REFERENCES inventory(id) ON DELETE SET NULL,
    FOREIGN KEY (component_type_id) REFERENCES component_types(id)
);

-- Backups tracking
CREATE TABLE IF NOT EXISTS backups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    filename VARCHAR(200) NOT NULL,
    filepath VARCHAR(500) NOT NULL,
    backup_type ENUM('Local', 'NAS') DEFAULT 'Local',
    size_bytes BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

-- Scrape jobs tracking
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    component_type VARCHAR(50),
    status ENUM('Pending', 'Running', 'Completed', 'Failed') DEFAULT 'Pending',
    items_found INT DEFAULT 0,
    items_added INT DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
