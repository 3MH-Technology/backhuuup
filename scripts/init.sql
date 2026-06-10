-- ────────────────────────────────────────────────────────────
-- Estidafa Platform — PostgreSQL Initialisation Script
-- ────────────────────────────────────────────────────────────
-- This is applied automatically by SQLAlchemy on first run.
-- Provided here for manual DB provisioning if needed.
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    username        VARCHAR(100) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    is_active       INTEGER      DEFAULT 1,
    is_admin        BOOLEAN      DEFAULT FALSE,
    device_fingerprint VARCHAR(255),
    reset_code      VARCHAR(10),
    reset_date      VARCHAR(20),
    reset_attempts_today INTEGER DEFAULT 0,
    reset_cooldown_until TIMESTAMPTZ,
    reset_code_expires_at TIMESTAMPTZ,
    reset_code_ip   VARCHAR(45),
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    ai_messages_today INTEGER DEFAULT 0,
    ai_date         VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS bots (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(255) NOT NULL UNIQUE,
    bot_type        VARCHAR(20)  NOT NULL CHECK (bot_type IN ('python', 'php', 'static')),
    status          VARCHAR(20)  DEFAULT 'stopped' CHECK (status IN ('running', 'stopped', 'crashed')),
    container_id    VARCHAR(64),
    main_file       TEXT,
    requirements    TEXT,
    is_upload       BOOLEAN      DEFAULT FALSE,
    upload_path     VARCHAR(512),
    webhook_url     VARCHAR(512),
    webhook_active  BOOLEAN      DEFAULT FALSE,
    webhook_token   TEXT,
    webhook_token_hash VARCHAR(64),
    restart_count   INTEGER      DEFAULT 0,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_bots_user_id      ON bots (user_id);
CREATE INDEX IF NOT EXISTS idx_bots_status        ON bots (status);
CREATE INDEX IF NOT EXISTS idx_bots_user_status   ON bots (user_id, status);
CREATE INDEX IF NOT EXISTS idx_bots_container_id  ON bots (container_id);
CREATE INDEX IF NOT EXISTS idx_bots_slug          ON bots (slug);
CREATE INDEX IF NOT EXISTS idx_bots_webhook_hash  ON bots (webhook_token_hash);
CREATE INDEX IF NOT EXISTS idx_users_username     ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_email        ON users (email);
