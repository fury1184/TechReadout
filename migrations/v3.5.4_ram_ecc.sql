-- TechReadOut v3.5.4 - RAM ECC/module type fields
-- Adds ECC/non-ECC and physical module type tracking to hardware_specs.

ALTER TABLE hardware_specs
    ADD COLUMN ram_ecc TINYINT(1) NULL AFTER ram_modules,
    ADD COLUMN ram_module_type VARCHAR(50) NULL AFTER ram_ecc;
