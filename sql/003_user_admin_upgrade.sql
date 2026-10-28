USE bic3tab;

-- Apply once to an existing installation. New installations already get these
-- columns from 002_create_tables.sql.
ALTER TABLE users
    ADD COLUMN isActive TINYINT(1) NOT NULL DEFAULT 1,
    ADD COLUMN password_reset_token_hash CHAR(64) NULL,
    ADD COLUMN password_reset_expires_at DATETIME NULL;
