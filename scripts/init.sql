-- ────────────────────────────────────────────────────────────
-- Estidafa Platform — PostgreSQL Initialisation Script
-- ────────────────────────────────────────────────────────────
-- This is applied automatically by SQLAlchemy on first run.
-- Provided here for manual DB provisioning if needed.
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    username        VARCHAR(100) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    is_active       INTEGER      DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bots (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    bot_type        VARCHAR(20)  NOT NULL CHECK (bot_type IN ('python', 'php')),
    status          VARCHAR(20)  DEFAULT 'stopped' CHECK (status IN ('running', 'stopped', 'crashed')),
    container_id    VARCHAR(64),
    main_file       TEXT,
    requirements    TEXT,
    restart_count   INTEGER      DEFAULT 0,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_bots_user_id      ON bots (user_id);
CREATE INDEX IF NOT EXISTS idx_bots_status        ON bots (status);
CREATE INDEX IF NOT EXISTS idx_bots_user_status   ON bots (user_id, status);
CREATE INDEX IF NOT EXISTS idx_bots_container_id  ON bots (container_id);
CREATE INDEX IF NOT EXISTS idx_users_email        ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_username     ON users (username);
