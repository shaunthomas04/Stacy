-- This is in 2NF but for the purpose of allowing Stacy to make her own infractions its fine this way for now
CREATE DATABASE IF NOT EXISTS stacy_hr_db;
USE stacy_hr_db;

-- 1. Servers Table
-- Each server gets its own personality/policy block
CREATE TABLE IF NOT EXISTS guilds (
    guild_id              VARCHAR(255) PRIMARY KEY,
    guild_name            VARCHAR(255),
    hr_policy_text        TEXT,
    decay_interval_minutes INT DEFAULT 1440,
    decay_amount          INT DEFAULT 5,
    last_decay_at         DATETIME DEFAULT NULL,
    sensitivity           ENUM('low', 'medium', 'high') DEFAULT 'low',
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- For existing installs: ALTER TABLE guilds ADD COLUMN sensitivity ENUM('low','medium','high') DEFAULT 'low';
-- For existing installs: ALTER TABLE infractions ADD COLUMN appealed BOOLEAN DEFAULT FALSE;

-- 2. Users Table
-- Tracking the social standing and current "pay" status
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(255),
    guild_id VARCHAR(255),
    username VARCHAR(255),
    social_credit_score INT DEFAULT 0,
    last_decay_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    current_status_role VARCHAR(100) DEFAULT 'HR Approved',
    PRIMARY KEY (user_id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

-- 3. Infractions Table
CREATE TABLE IF NOT EXISTS infractions (
    infraction_id INT AUTO_INCREMENT PRIMARY KEY,
    guild_id VARCHAR(255),
    user_id VARCHAR(255),
    violation_context TEXT,      
    stacy_inference TEXT,        
    severity_level ENUM('Low', 'Medium', 'High', 'Critical'),
    score_penalty INT,
    appealed BOOLEAN DEFAULT FALSE,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
    FOREIGN KEY (user_id, guild_id) REFERENCES users(user_id, guild_id)
);
