-- db-per-service: each service owns its own logical database on the shared Postgres.
-- Runs once on a fresh data volume (docker-entrypoint-initdb.d). The `platform` role and
-- `platform` database are created by the container's POSTGRES_* env vars.

CREATE DATABASE example_service OWNER platform;
CREATE DATABASE langfuse OWNER platform;
CREATE DATABASE identity OWNER platform;
CREATE DATABASE audit OWNER platform;
