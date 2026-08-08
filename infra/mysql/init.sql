-- PulseSearch system-of-record schema and CDC user.
-- Runs automatically on first container start (docker-entrypoint-initdb.d).

CREATE DATABASE IF NOT EXISTS pulsesearch
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE pulsesearch;

-- One row per (wiki, page). Repeated edits UPDATE the row and bump edit_count,
-- producing a realistic mix of INSERT/UPDATE change events for Debezium.
CREATE TABLE IF NOT EXISTS pages (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    wiki         VARCHAR(64)     NOT NULL,
    title        VARCHAR(512)    NOT NULL,
    title_url    VARCHAR(1024)   NULL,
    last_comment TEXT            NULL,
    last_user    VARCHAR(255)    NULL,
    event_type   VARCHAR(32)     NULL,
    namespace    INT             NOT NULL DEFAULT 0,
    is_bot       TINYINT(1)      NOT NULL DEFAULT 0,
    is_minor     TINYINT(1)      NOT NULL DEFAULT 0,
    length_new   INT             NULL,
    edit_count   INT             NOT NULL DEFAULT 1,
    first_seen   DATETIME(3)     NULL,
    event_time   DATETIME(3)     NULL,
    updated_at   TIMESTAMP(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                     ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uq_wiki_title (wiki, title(191)),
    KEY idx_event_time (event_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Dedicated least-privilege user for Debezium CDC.
CREATE USER IF NOT EXISTS 'debezium'@'%' IDENTIFIED BY 'debezium';
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT
    ON *.* TO 'debezium'@'%';

-- Ensure the application user can read/write the schema.
GRANT ALL PRIVILEGES ON pulsesearch.* TO 'pulse'@'%';
FLUSH PRIVILEGES;
