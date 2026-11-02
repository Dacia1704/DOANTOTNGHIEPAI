-- =====================================================================
-- Tao schema va user cho AI service (FastAPI).
-- Chay mot lan: mysql -u root -p < scripts/setup_database.sql
-- =====================================================================

CREATE DATABASE IF NOT EXISTS rental_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Doi mat khau truoc khi chay production.
CREATE USER IF NOT EXISTS 'ai_svc'@'%' IDENTIFIED BY 'AI_SERVICE_PASSWORD';

-- Toan quyen tren schema AI (Alembic quan ly).
GRANT ALL PRIVILEGES ON rental_ai.* TO 'ai_svc'@'%';

-- Chi doc cac bang nghiep vu can thiet.
GRANT SELECT ON rental_db.listings                TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.rooms                   TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.properties              TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.property_amenities       TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.room_amenities          TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.amenities               TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.room_types              TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.provinces               TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.wards                   TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.tenant_profiles         TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.tenant_profile_amenities TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.tenant_profile_wards    TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.listing_view_history    TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.saved_listings          TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.listing_contact_events  TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.listing_search_history  TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.user_roles              TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.roles                   TO 'ai_svc'@'%';

-- Bang users: chi cap cac cot can de kiem tra token.
-- KHONG cap password, phone, email, citizen_id.
GRANT SELECT (id, status, auth_version) ON rental_db.users TO 'ai_svc'@'%';

FLUSH PRIVILEGES;
