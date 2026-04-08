USE stacy_hr_db;

SELECT * FROM users;

-- 1. See all Servers and their HR Constitutions
SELECT * FROM guilds;

-- 2. See the 'Most Wanted' (Users with the highest social debt across all servers)
-- SELECT 
--     username, 
--     guild_id, 
--     social_credit_score AS social_debt, 
--     current_status_role 
-- FROM users 
-- ORDER BY social_credit_score DESC;

-- 3. THE "HR MASTER REPORT" 
-- This joins everything: User info, their infractions, and the context of their crimes.
-- SELECT 
--     g.guild_name,
--     u.username,
--     u.social_credit_score AS total_debt,
--     u.current_status_role,
--     i.timestamp AS infraction_date,
--     i.severity_level,
--     i.violation_context AS message_sent,
--     i.stacy_inference AS why_stacy_is_mad,
--     i.score_penalty
-- FROM users u
-- JOIN guilds g ON u.guild_id = g.guild_id
-- LEFT JOIN infractions i ON u.user_id = i.user_id AND u.guild_id = i.guild_id
-- ORDER BY g.guild_name, u.social_credit_score DESC, i.timestamp DESC;
