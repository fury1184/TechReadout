-- TechReadout v3.0.1 Migration: Sync inventory status enum with new values
-- Run this against your live database BEFORE deploying the updated code.

-- Step 1: Temporarily change column to VARCHAR so we can remap values freely
ALTER TABLE inventory MODIFY COLUMN status VARCHAR(20) NOT NULL DEFAULT 'Unverified';

-- Step 2: Remap old values to new values
UPDATE inventory SET status = 'Verified'   WHERE status = 'Available';
UPDATE inventory SET status = 'Installed'  WHERE status = 'In Use';
UPDATE inventory SET status = 'Verified'   WHERE status = 'Reserved';
UPDATE inventory SET status = 'Dead'       WHERE status = 'Disposed';
-- 'Sold' stays as 'Sold' — no change needed

-- Step 3: Restore as ENUM with new values
ALTER TABLE inventory MODIFY COLUMN status ENUM('Unverified','Verified','Installed','Missing','Sold','Dead') NOT NULL DEFAULT 'Unverified';
