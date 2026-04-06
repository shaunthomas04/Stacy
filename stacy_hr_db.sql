-- This is in 2NF but for the purpose of allowing Stacy to make her own infractions its fine this way for now

CREATE DATABASE IF NOT EXISTS stacy_hr_db;
USE stacy_hr_db;

-- 1. Servers Table
-- Each server gets its own personality/policy block
CREATE TABLE IF NOT EXISTS guilds (
    guild_id VARCHAR(255) PRIMARY KEY,
    guild_name VARCHAR(255),
    hr_policy_text TEXT, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
    FOREIGN KEY (user_id, guild_id) REFERENCES users(user_id, guild_id)
);

-- 1. See all Servers and their HR Constitutions
SELECT * FROM guilds;

-- 2. See the 'Most Wanted' (Users with the highest social debt across all servers)
SELECT 
    username, 
    guild_id, 
    social_credit_score AS social_debt, 
    current_status_role 
FROM users 
ORDER BY social_credit_score DESC;

-- 3. THE "HR MASTER REPORT" 
-- This joins everything: User info, their infractions, and the context of their crimes.
SELECT 
    g.guild_name,
    u.username,
    u.social_credit_score AS total_debt,
    u.current_status_role,
    i.timestamp AS infraction_date,
    i.severity_level,
    i.violation_context AS message_sent,
    i.stacy_inference AS why_stacy_is_mad,
    i.score_penalty
FROM users u
JOIN guilds g ON u.guild_id = g.guild_id
LEFT JOIN infractions i ON u.user_id = i.user_id AND u.guild_id = i.guild_id
ORDER BY g.guild_name, u.social_credit_score DESC, i.timestamp DESC;