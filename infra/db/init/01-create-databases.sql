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
-- Separate from `agent_registry`, same reasoning as `audit_test`: this service's tests wipe the
-- table between runs for isolation, which must never touch a database anything real writes to.
CREATE DATABASE agent_registry_test OWNER platform;
CREATE DATABASE skill_registry OWNER platform;
CREATE DATABASE skill_registry_test OWNER platform;
CREATE DATABASE eval_registry OWNER platform;
CREATE DATABASE eval_registry_test OWNER platform;
-- ContextForge (Agent Gateway harness) manages its own schema/migrations against this DB.
CREATE DATABASE agent_gateway OWNER platform;
CREATE DATABASE approvals OWNER platform;
CREATE DATABASE approvals_test OWNER platform;
CREATE DATABASE sandbox OWNER platform;
CREATE DATABASE sandbox_test OWNER platform;
-- Temporal manages its own schema against these two (main persistence + visibility store —
-- Temporal's own architecture splits them, not a db-per-service choice we made).
CREATE DATABASE temporal OWNER platform;
CREATE DATABASE temporal_visibility OWNER platform;
