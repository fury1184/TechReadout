-- TechReadout v3.10.0 Migration: current value tracking, separate from purchase price
-- Run this against your live database BEFORE deploying the updated code.
--
-- price_is_estimate now describes current_value (not purchase_price): True when
-- current_value was last set via the eBay fetch, False when entered manually.
-- Existing rows keep their price_is_estimate meaning (it was previously about
-- purchase_price); re-check/re-apply pricing on items where that distinction matters.

ALTER TABLE inventory
    ADD COLUMN current_value DECIMAL(10, 2) NULL,
    ADD COLUMN current_value_updated_at TIMESTAMP NULL;
