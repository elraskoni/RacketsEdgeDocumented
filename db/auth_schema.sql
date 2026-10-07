-- API-key tables the public API needs (require_api_key). In production they are copied from the
-- pre-3.0 database and are not part of db/racketedge_schema.sql; this mirrors their columns
-- (SHOW CREATE TABLE, 2026-10-06) without the users table and its foreign key.

CREATE TABLE IF NOT EXISTS plans (
  plan_id          VARCHAR(64)  NOT NULL,
  name             VARCHAR(255) NOT NULL,
  rpm_limit        INT          NOT NULL,
  daily_quota      INT          NOT NULL,
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  price_cents      INT          NOT NULL DEFAULT 0,
  stripe_price_id  VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (plan_id)
);

CREATE TABLE IF NOT EXISTS organizations (
  org_id      BIGINT       NOT NULL AUTO_INCREMENT,
  name        VARCHAR(255) NOT NULL,
  plan_id     VARCHAR(64)  NOT NULL DEFAULT 'plan-trial',
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (org_id),
  UNIQUE KEY uq_org_name (name)
);

CREATE TABLE IF NOT EXISTS api_keys (
  api_key_id    BIGINT        NOT NULL AUTO_INCREMENT,
  org_id        BIGINT        NOT NULL,
  user_id       BIGINT        DEFAULT NULL,
  product_id    VARCHAR(64)   NOT NULL,
  key_prefix    VARCHAR(16)   NOT NULL,
  key_hash      VARBINARY(32) NOT NULL,
  name          VARCHAR(255)  NOT NULL,
  status        VARCHAR(16)   NOT NULL DEFAULT 'active',
  created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_used_at  TIMESTAMP     NULL DEFAULT NULL,
  PRIMARY KEY (api_key_id),
  UNIQUE KEY uq_api_keys_hash (org_id, key_hash),
  CONSTRAINT fk_api_keys_org FOREIGN KEY (org_id) REFERENCES organizations (org_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS usage_records (
  usage_id    BIGINT      NOT NULL AUTO_INCREMENT,
  org_id      BIGINT      NOT NULL,
  product_id  VARCHAR(64) NOT NULL,
  category    VARCHAR(32) NOT NULL,
  date        DATE        NOT NULL,
  requests    INT         NOT NULL DEFAULT 0,
  errors      INT         NOT NULL DEFAULT 0,
  created_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (usage_id),
  UNIQUE KEY uq_usage_dim (org_id, product_id, category, date),
  CONSTRAINT fk_usage_org FOREIGN KEY (org_id) REFERENCES organizations (org_id) ON DELETE CASCADE
);
