-- =============================================================================
-- node-red/sql/init.sql — PostgreSQL 16 DDL, ML Extensions & Operational Views
-- WEG PLC500 + Node-RED + FastAPI ML + PostgreSQL Supervisory Pipeline
-- =============================================================================

CREATE TABLE IF NOT EXISTS telemetria_sensores (
  id                  BIGSERIAL PRIMARY KEY,
  capturado_em        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  di_presenca         BOOLEAN NOT NULL,
  di_nivel_alto       BOOLEAN NOT NULL,
  di_sobretemp        BOOLEAN NOT NULL,
  di_emergencia_ok    BOOLEAN NOT NULL,
  flag_emerg_travada  BOOLEAN NOT NULL,
  flag_watchdog       BOOLEAN NOT NULL,
  cnt_presenca        INTEGER NOT NULL,
  cnt_nivel_alto      INTEGER NOT NULL,
  cnt_sobretemp       INTEGER NOT NULL,
  cnt_emergencia      INTEGER NOT NULL DEFAULT 0,
  estado_clp          SMALLINT NOT NULL,
  mascara_do_real     SMALLINT NOT NULL,
  cod_intertrav       SMALLINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tel_capturado
  ON telemetria_sensores (capturado_em DESC);

CREATE TABLE IF NOT EXISTS janelas_analiticas (
  id                     BIGSERIAL PRIMARY KEY,
  janela_inicio          TIMESTAMPTZ NOT NULL,
  janela_fim             TIMESTAMPTZ NOT NULL,
  amostras_k             INTEGER NOT NULL,
  taxa_presenca_min      DOUBLE PRECISION NOT NULL,
  duty_nivel             DOUBLE PRECISION NOT NULL,
  duty_sobretemp         DOUBLE PRECISION NOT NULL,
  zscore_presenca        DOUBLE PRECISION NOT NULL,
  severidade_estatistica SMALLINT NOT NULL CHECK (severidade_estatistica BETWEEN 0 AND 3),
  chattering_index       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  anomaly_score          DOUBLE PRECISION,
  is_anomaly             BOOLEAN NOT NULL DEFAULT FALSE,
  ml_severity            SMALLINT CHECK (ml_severity IS NULL OR ml_severity BETWEEN 0 AND 3),
  ml_confidence          DOUBLE PRECISION CHECK (
    ml_confidence IS NULL OR (ml_confidence >= 0.0 AND ml_confidence <= 1.0)
  ),
  model_version          VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_janelas_fim
  ON janelas_analiticas (janela_fim DESC);

CREATE TABLE IF NOT EXISTS diagnosticos_ia (
  id                 BIGSERIAL PRIMARY KEY,
  janela_id          BIGINT NOT NULL REFERENCES janelas_analiticas (id),
  criado_em          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  severidade_final   VARCHAR(16) NOT NULL CHECK (
    severidade_final IN ('NORMAL', 'ATENCAO', 'CRITICO', 'EMERGENCIA')
  ),
  origem_decisao     VARCHAR(16) NOT NULL CHECK (
    origem_decisao IN ('LLM', 'FALLBACK_STATS', 'ML_FASTAPI', 'ML_HYBRID')
  ),
  diagnostico        TEXT NOT NULL,
  causa_provavel     TEXT NOT NULL,
  payload_validado   JSONB NOT NULL,
  relatorio_enviado  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_diag_janela_id
  ON diagnosticos_ia (janela_id);

CREATE INDEX IF NOT EXISTS idx_diag_criado_em
  ON diagnosticos_ia (criado_em DESC);

CREATE TABLE IF NOT EXISTS comandos_atuadores (
  id                 BIGSERIAL PRIMARY KEY,
  diagnostico_id     BIGINT REFERENCES diagnosticos_ia (id),
  enviado_em         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cmd_motor          BOOLEAN NOT NULL,
  cmd_valvula        BOOLEAN NOT NULL,
  cmd_alarme         BOOLEAN NOT NULL,
  severidade_hr2     SMALLINT NOT NULL CHECK (severidade_hr2 BETWEEN 0 AND 3),
  mascara_do_confirm SMALLINT,
  veto_local_clp     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_cmd_enviado_em
  ON comandos_atuadores (enviado_em DESC);

-- -----------------------------------------------------------------------------
-- Operational Views
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW vw_baseline_24h AS
SELECT
  CASE
    WHEN COUNT(*) >= 5 THEN AVG(taxa_presenca_min)
    ELSE 12.0
  END::DOUBLE PRECISION AS baseline_mu_presenca,
  CASE
    WHEN COUNT(*) >= 5 THEN COALESCE(NULLIF(STDDEV_SAMP(taxa_presenca_min), 0.0), 2.5)
    ELSE 2.5
  END::DOUBLE PRECISION AS baseline_sigma_presenca,
  COUNT(*)::INTEGER AS amostras_nominais_24h,
  MAX(janela_fim) AS ultima_janela_em
FROM janelas_analiticas
WHERE severidade_estatistica = 0
  AND janela_fim >= NOW() - INTERVAL '24 hours';

CREATE OR REPLACE VIEW vw_dashboard_status AS
SELECT
  t.id AS telemetria_id,
  t.capturado_em,
  t.di_presenca,
  t.di_nivel_alto,
  t.di_sobretemp,
  t.di_emergencia_ok,
  t.flag_emerg_travada,
  t.flag_watchdog,
  t.cnt_presenca,
  t.cnt_nivel_alto,
  t.cnt_sobretemp,
  t.cnt_emergencia,
  t.estado_clp,
  t.mascara_do_real,
  t.cod_intertrav,
  j.id AS janela_id,
  j.janela_inicio,
  j.janela_fim,
  j.amostras_k,
  j.taxa_presenca_min,
  j.duty_nivel,
  j.duty_sobretemp,
  j.zscore_presenca,
  j.chattering_index,
  j.severidade_estatistica,
  j.anomaly_score,
  j.is_anomaly,
  j.ml_severity,
  j.ml_confidence,
  j.model_version,
  d.id AS diagnostico_id,
  d.criado_em AS diagnostico_em,
  d.severidade_final,
  d.origem_decisao,
  d.diagnostico,
  d.causa_provavel,
  d.relatorio_enviado,
  c.id AS comando_id,
  c.enviado_em AS comando_em,
  c.cmd_motor,
  c.cmd_valvula,
  c.cmd_alarme,
  c.severidade_hr2,
  c.mascara_do_confirm,
  c.veto_local_clp
FROM (SELECT 1 AS anchor) _a
LEFT JOIN (SELECT * FROM telemetria_sensores ORDER BY capturado_em DESC, id DESC LIMIT 1) t ON TRUE
LEFT JOIN (SELECT * FROM janelas_analiticas ORDER BY janela_fim DESC, id DESC LIMIT 1) j ON TRUE
LEFT JOIN (SELECT * FROM diagnosticos_ia ORDER BY criado_em DESC, id DESC LIMIT 1) d ON TRUE
LEFT JOIN (SELECT * FROM comandos_atuadores ORDER BY enviado_em DESC, id DESC LIMIT 1) c ON TRUE;

CREATE OR REPLACE VIEW vw_auditoria_vetos AS
SELECT
  c.id AS comando_id,
  c.enviado_em,
  c.diagnostico_id,
  d.janela_id,
  d.severidade_final,
  d.origem_decisao,
  d.diagnostico,
  d.causa_provavel,
  c.cmd_motor,
  c.cmd_valvula,
  c.cmd_alarme,
  c.severidade_hr2,
  c.mascara_do_confirm,
  c.veto_local_clp,
  ((c.mascara_do_confirm & 1) <> 0) AS do0_motor_real,
  ((c.mascara_do_confirm & 2) <> 0) AS do1_valvula_real,
  ((c.mascara_do_confirm & 4) <> 0) AS do2_alarme_real,
  ((c.mascara_do_confirm & 8) <> 0) AS do3_status_real
FROM comandos_atuadores c
LEFT JOIN diagnosticos_ia d ON d.id = c.diagnostico_id
WHERE c.veto_local_clp = TRUE
   OR (
     c.mascara_do_confirm IS NOT NULL AND (
       (c.cmd_motor   AND (c.mascara_do_confirm & 1) = 0) OR
       (c.cmd_valvula AND (c.mascara_do_confirm & 2) = 0) OR
       (c.cmd_alarme  AND (c.mascara_do_confirm & 4) = 0)
     )
   )
ORDER BY c.enviado_em DESC, c.id DESC;
