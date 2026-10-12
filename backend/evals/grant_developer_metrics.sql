-- For databases whose analytics roles were provisioned BEFORE migration 0007: run as a DBA
-- after `alembic upgrade head` to expose the developer-metrics views. Fresh installs get these
-- grants from provision_analytics.sql instead.
GRANT SELECT ON analytics_masked.pull_requests, analytics_masked.deployments,
    analytics_masked.dora_weekly TO aoi_analytics_masked;
GRANT SELECT ON analytics_owner.pull_requests, analytics_owner.deployments,
    analytics_owner.dora_weekly TO aoi_analytics_owner;
