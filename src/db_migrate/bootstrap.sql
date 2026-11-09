-- One-time database bootstrap, run as the RDS master user (SDD §4.6). Idempotent: safe to run again.
-- Roles log in with IAM tokens (rds_iam) – no passwords exist for application roles (ADR-007).
CREATE EXTENSION IF NOT EXISTS vector;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'migrator') THEN CREATE ROLE migrator LOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'match_app') THEN CREATE ROLE match_app LOGIN; END IF;
END $$;

GRANT rds_iam TO migrator, match_app;

-- The master user may act for migrator so it can create migrator-owned schemas and default privileges.
GRANT migrator TO CURRENT_USER;

CREATE SCHEMA IF NOT EXISTS meta AUTHORIZATION migrator;
CREATE SCHEMA IF NOT EXISTS tracker AUTHORIZATION migrator;
CREATE SCHEMA IF NOT EXISTS vec AUTHORIZATION migrator;

-- match_app: read/write vectors, read the tracker (to exclude postings already applied to).
GRANT USAGE ON SCHEMA vec, tracker TO match_app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA vec GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO match_app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA tracker GRANT SELECT ON TABLES TO match_app;
