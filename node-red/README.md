# Node-RED Supervisory Orchestrator (`node-red/`)

Node-RED v4 supervisory layer for the **WEG PLC500** Modbus TCP + PostgreSQL 16 + FastAPI ML + optional LLM hybrid pipeline.

## Directory Contents

- `flows.json`: 5-tab Node-RED v4 flow implementing 500 ms acquisition, 60 s hybrid analysis, closed-loop actuation & audit, Dashboard 2.0, and test/simulator utilities.
- `settings.js`: Runtime configuration aligning environment defaults with `.env.example` and precompiling `schemas/diagnostico_atuacao.schema.json` with `Ajv2020` (`ajv/dist/2020` + `ajv-formats`) into `functionGlobalContext.validateDiagnostico`.
- `package.json`: Node-RED dependencies (`node-red-contrib-modbus`, `@flowfuse/node-red-dashboard`, `node-red-contrib-postgresql`, `ajv`, `ajv-formats`).
- `Dockerfile`: Builds self-contained `nodered/node-red:4.1.18` image with `/data/node_modules`, `settings.js`, `flows.json`, and `schemas/`.
- `sql/init.sql`: PostgreSQL 16 DDL (`telemetria_sensores`, `janelas_analiticas`, `diagnosticos_ia`, `comandos_atuadores`), `idx_tel_capturado`, ML columns, and views (`vw_baseline_24h`, `vw_dashboard_status`, `vw_auditoria_vetos`).
- `schemas/diagnostico_atuacao.schema.json`: JSON Schema Draft 2020-12 contract `DiagnosticoAtuacaoPLC500`.
- `flows_cred.json.example`: Placeholder credential template (PostgreSQL credentials are read directly from environment variables via `env` TypedInputs).

## 5-Tab Flow Architecture

1. **Tab 1 — `Aquisição Modbus TCP (500 ms)`**
   - Polls `FC02` (`DI 0..5`) and `FC04` (`IR 0..7`) every `500 ms` (`Tp`) using shared `modbus-client` (`${PLC_HOST}:${PLC_PORT}`, Unit ID `${PLC_UNIT_ID}`, timeout `1500 ms`, reconnect `2000 ms`).
   - Persists raw telemetry into `telemetria_sensores`.
   - Writes cyclic `FC06` heartbeat `1..65535` to `HR[0]` (pausable via `global.get("pause_heartbeat")` for `CT-09`).
   - Emits immediate trigger to Tab 2 on rising emergency/overtemp/watchdog edges and streams live state to Tab 4 Dashboard.

2. **Tab 2 — `Análise Híbrida (60 s + ML + LLM)`**
   - Triggers every `60 s` (or on critical edge / Tab 5 test inject), querying the last 60 s window joined with `vw_baseline_24h`.
   - Computes `deltaMod16`, arrival rate `lambda1`, duty cycles `D2`/`D3`, `chattering_index`, regularized Z-score `z1` (with `12.0`/`2.5` fallback and `SEM_DADOS` empty-window guard), and deterministic severity `0..3` on `msg.analise`.
   - Calls `POST ${ML_API_URL}/predict` (graceful degradation on HTTP/network error) and inserts `janelas_analiticas RETURNING id`.
   - Routes `acionar_llm === true` through optional LLM branch (`LLM_PROVIDER`: `none`, `openai`, or `ollama`) -> `Ajv2020` `validateDiagnostico` -> Deterministic Fallback (`FALLBACK_STATS`) on error/timeout/invalid JSON -> **Monotonicity Safety Guardian** (`sev_final >= sev_estatistica`, blocks `cmd_motor=true` on overtemp, sustained high level, emergency, or watchdog).

3. **Tab 3 — `Atuação, Auditoria & Relatórios`**
   - Writes `FC15` (`Coils 0..2`) and `FC06` (`HR[2] = uiSeveridade_IA`), waits `50 ms` for PLC 10 ms scan settle, and reads back `IR[4..7]`.
   - Computes `veto_local_clp` comparing commanded coils against `wMascara_DO_Real` (`IR[5]`) and `uiCod_Intertrav` (`IR[7]`).
   - Inserts `diagnosticos_ia` and `comandos_atuadores`.
   - When `requer_relatorio` is true (`CT-10`), renders a Markdown incident report via `template` node, updates `diagnosticos_ia.relatorio_enviado = TRUE`, and optionally dispatches to Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).

4. **Tab 4 — `Dashboard Operacional`**
   - Accessible at `http://localhost:1880/dashboard/operacao` (port `${NR_PORT}`).
   - Displays 24 Vcc field I/O LEDs (`DI0..DI3`, `DO0..DO3`), PLC FSM state (`uiEstado_CLP`), interlock code (`uiCod_Intertrav`), gauges (`taxa_presenca_min`, `zscore_presenca`), trend chart, latest diagnosis/Markdown report, and remote controls (`Coil[4]` `xCmd_HabRemoto`, `Coil[3]` `xCmd_ResetFalha`).

5. **Tab 5 — `Utilitários de Teste & Simulador`**
   - Provides one-click inject triggers and HTTP calls to `${PLC_SIM_API_URL}` for `CT-01`..`CT-10`, including `CT-05` valid JSON diagnosis, `CT-06` malformed JSON/timeout fallback, `CT-09` heartbeat pause/resume, and simulator fault injection (`B1` bounce/chattering, `B2` high level, `B3` overtemp, `B4` E-stop NF open, reset nominal).

## Key Environment Variables (`.env.example`)

| Variable | Default | Purpose |
|---|---|---|
| `PLC_HOST` / `PLC_PORT` / `PLC_UNIT_ID` | `192.168.10.10` / `502` / `1` | Modbus TCP target (`plc-simulator` in Compose `sim` profile) |
| `NR_MODBUS_TIMEOUT_MS` / `NR_MODBUS_RECONNECT_MS` | `1500` / `2000` | Modbus client timeouts |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` | `postgres` / `5432` / `plc500_telemetry` | PostgreSQL connection |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `plc500_user` / *(set in `.env`)* | PostgreSQL credentials |
| `ML_API_URL` | `http://ml-api:8000` | FastAPI ML inference service |
| `PLC_SIM_API_URL` | `http://plc-simulator:8001` | Python simulator fault-injection API |
| `LLM_PROVIDER` / `LLM_TIMEOUT_MS` | `none` / `8000` | `none`, `openai`, or `ollama` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | *(empty)* | Optional Markdown report delivery (`CT-10`) |
