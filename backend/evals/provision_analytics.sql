-- Run as a DBA after alembic upgrade head. Deliberately fails if roles already
-- exist: inspect existing privileges rather than silently trusting reused roles.
CREATE ROLE aoi_analytics_masked NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE aoi_analytics_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE aoi_analytics_masked SET default_transaction_read_only = on;
ALTER ROLE aoi_analytics_owner SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA analytics_masked TO aoi_analytics_masked;
GRANT SELECT ON analytics_masked.tasks, analytics_masked.risks, analytics_masked.blockers TO aoi_analytics_masked;
GRANT USAGE ON SCHEMA analytics_owner TO aoi_analytics_owner;
GRANT SELECT ON analytics_owner.tasks, analytics_owner.risks, analytics_owner.blockers TO aoi_analytics_owner;
-- Provision LOGIN and separate generated passwords through your secret manager.
-- Never grant these roles membership in application/owner roles or base tables.
