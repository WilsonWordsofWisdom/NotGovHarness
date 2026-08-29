-- db-per-service: each service owns its own logical database on the shared Postgres.
-- Runs once on a fresh data volume (docker-entrypoint-initdb.d). The `platform` role and
-- `platform` database are created by the container's POSTGRES_* env vars.

CREATE DATABASE example_service OWNER platform;
CREATE DATABASE langfuse OWNER platform;
CREATE DATABASE identity OWNER platform;
CREATE DATABASE audit OWNER platform;
-- Separate from `audit`: audit-service's own tests (test_writer.py, test_api.py) wipe this table
-- between tests for isolation, which is only safe against a DB nothing else writes to. The live
-- audit-service container (and test_live_stack.py, which exercises it for real) uses `audit`.
CREATE DATABASE audit_test OWNER platform;
CREATE DATABASE agent_registry OWNER platform;
