-- TechReadout v2.1.0 Migration: Inventory Verification Status
-- Run this BEFORE deploying the updated code.
--
-- Maps existing statuses:
--   Available  → Unverified
--   In Use     → Installed
--   Reserved   → Unverified
--   Sold       → Sold
--   Disposed   → Dead

-- Step 1: Add the new enum values to the column
ALTER TABLE inventory
    MODIFY COLUMN status ENUM(
        'Available', 'In Use', 'Reserved', 'Sold', 'Disposed',
        'Unverified', 'Verified', 'Installed', 'Missing', 'Dead'
    ) NOT NULL DEFAULT 'Unverified';

-- Step 2: Migrate existing data
UPDATE inventory SET status = 'Installed'  WHERE status = 'In Use';
UPDATE inventory SET status = 'Unverified' WHERE status = 'Available';
UPDATE inventory SET status = 'Unverified' WHERE status = 'Reserved';
UPDATE inventory SET status = 'Dead'       WHERE status = 'Disposed';
-- 'Sold' stays as 'Sold', no change needed

-- Step 3: Remove old enum values now that no rows reference them
ALTER TABLE inventory
    MODIFY COLUMN status ENUM(
        'Unverified', 'Verified', 'Installed', 'Missing', 'Sold', 'Dead'
    ) NOT NULL DEFAULT 'Unverified';
