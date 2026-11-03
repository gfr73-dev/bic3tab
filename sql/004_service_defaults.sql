USE bic3tab;

CREATE TABLE IF NOT EXISTS service_defaults (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    BICACT CHAR(3) NOT NULL,
    BICSSERV CHAR(6) NOT NULL,
    BICSPROD CHAR(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_service_defaults_bicact (BICACT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Preserve existing audit rows while allowing the six-character defaults.
ALTER TABLE change_log
    MODIFY BICSSERV_OLD CHAR(6) NULL,
    MODIFY BICSPROD_OLD CHAR(6) NULL,
    MODIFY BICSSERV_NEW CHAR(6) NULL,
    MODIFY BICSPROD_NEW CHAR(6) NULL;
