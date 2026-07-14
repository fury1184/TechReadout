-- TechReadout v3.1.0 Migration: eBay price estimate support
-- Run this against your live database BEFORE deploying the updated code.

-- Add estimate flag to inventory
ALTER TABLE inventory
    ADD COLUMN price_is_estimate TINYINT(1) NOT NULL DEFAULT 0;

-- Price cache table for eBay API results (24-hour TTL)
CREATE TABLE IF NOT EXISTS price_cache (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    search_query VARCHAR(300)   NOT NULL,
    ebay_price   DECIMAL(10, 2) NOT NULL,
    listing_count INT,
    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_price_cache_query (search_query),
    INDEX idx_price_cache_fetched (fetched_at)
);
