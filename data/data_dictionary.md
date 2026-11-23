# Data Dictionary — WEG PLC500 Synthetic Industrial Telemetry

## 1. Raw 2 Hz Telemetry (`data/raw/raw_telemetry_2hz.csv.gz` & `raw_sample_500.csv`)

- **Rows**: `129,600` (18.0 hours @ 2.0 Hz, `dt = 0.5 s`, `seed = 42`)
- **Sample file**: `raw_sample_500.csv` contains the first `500` rows (`0.0 s .. 249.5 s`) covering `nominal` followed by `cadence_drift`.

| Column | Type | Unit / Range | PLC500 / Modbus Source | Description |
|---|---|---|---|---|
| `sample_idx` | `int64` | `0..129599` | — | Monotonic 2 Hz sample index |
| `time_s` | `float64` | `0.0..64799.5` s | — | Elapsed simulation time in seconds |
| `timestamp_utc` | `string` | ISO-8601 UTC | — | Synthetic UTC timestamp (`2026-01-15T00:00:00Z` + `time_s`) |
| `split` | `string` | `train`, `purge`, `val`, `test` | — | Strict temporal partition |
| `scenario` | `string` | 10 scenarios | — | Active physical/OT scenario label |
| `di_presenca` | `bool` | `False`/`True` | `FC02` addr `0` (`DI0`) | Debounced presence sensor `B1_SensPresenca` |
| `di_nivel_alto` | `bool` | `False`/`True` | `FC02` addr `1` (`DI1`) | Debounced high-level sensor `B2_SensNivelAlto` |
| `di_sobretemp` | `bool` | `False`/`True` | `FC02` addr `2` (`DI2`) | Debounced overtemperature sensor `B3_SensSobretemp` |
| `di_emergencia_ok` | `bool` | `False`/`True` | `FC02` addr `3` (`DI3`) | Fail-safe NC E-stop (`False` = trip/wire break) |
| `flag_emerg_travada` | `bool` | `False`/`True` | `FC02` addr `4` | Latched emergency interlock flag |
| `flag_watchdog` | `bool` | `False`/`True` | `FC02` addr `5` | Active Modbus heartbeat watchdog timeout flag |
| `cnt_presenca` | `uint16` | `0..65535` | `FC04` addr `0` (`IR[0]`) | Modular 16-bit rising-edge counter for `B1` |
| `cnt_nivel_alto` | `uint16` | `0..65535` | `FC04` addr `1` (`IR[1]`) | Modular 16-bit rising-edge counter for `B2` |
| `cnt_sobretemp` | `uint16` | `0..65535` | `FC04` addr `2` (`IR[2]`) | Modular 16-bit rising-edge counter for `B3` |
| `cnt_emergencia` | `uint16` | `0..65535` | `FC04` addr `3` (`IR[3]`) | Modular 16-bit trip counter for `B4` (`NOT DI3`) |
| `estado_clp` | `int` | `0..4` | `FC04` addr `4` (`IR[4]`) | PLC FSM state (`0=INIT..4=EMERGENCIA_TRAV`) *(audit only, excluded from ML `X`)* |
| `mascara_do_real` | `int` | `0..15` | `FC04` addr `5` (`IR[5]`) | Real output bitmask `DO0..DO3` *(audit only, excluded from ML `X`)* |
| `cod_intertrav` | `int` | `0..3` | `FC04` addr `7` (`IR[7]`) | Active local interlock code *(audit only, excluded from ML `X`)* |

## 2. Processed 60 s Sliding-Window Features (`data/processed/window_features_60s.csv`)

- **Rows**: `3,688` windows (`W = 60.0 s`, `K = 120` samples, `stride = 15.0 s`)
- **Zero Train/Serve Skew**: `MODEL_FEATURE_COLUMNS` (`10` columns) are computed identically in raw 2 Hz mode and summary `/predict` mode.

### Metadata & Partition Columns (Excluded from ML `X`)
- `window_id`, `split`, `window_start_s`, `window_end_s`, `raw_start_idx`, `raw_end_idx`, `timestamp_start`, `timestamp_end`, `amostras_k`

### Model Input Features (`MODEL_FEATURE_COLUMNS` — 10 Leakage-Free Features)
| Column | Range | Formula / Definition |
|---|---|---|
| `taxa_presenca_min` | `>= 0.0` | `(deltaMod16(cnt_presenca_end, cnt_presenca_start) / 60.0) * 60.0` [events/min] |
| `zscore_presenca` | `float` | `(taxa_presenca_min - 12.0) / (2.5 + 0.01)` |
| `abs_zscore_presenca` | `>= 0.0` | `abs(zscore_presenca)` |
| `duty_nivel` | `[0.0, 1.0]` | Mean of `di_nivel_alto` across window samples |
| `duty_sobretemp` | `[0.0, 1.0]` | Mean of `di_sobretemp` across window samples |
| `chattering_index` | `[0.0, 1.0]` | Bounded rapid oscillation & multi-pulse rate excess index |
| `veto_emergencia` | `(0, 1)` | `1` if `flag_emerg_travada` or `not di_emergencia_ok` at window end |
| `veto_sobretemp` | `(0, 1)` | `1` if `duty_sobretemp > 0` or `di_sobretemp` at window end |
| `veto_watchdog` | `(0, 1)` | `1` if `flag_watchdog` at window end |
| `veto_nivel_sustentado` | `(0, 1)` | `1` if `duty_nivel >= 0.30` |

### Audit Columns (Retained for OT/IT Traceability, Excluded from ML `X`)
- `delta_cnt_presenca`, `delta_cnt_nivel`, `delta_cnt_sobretemp`, `delta_cnt_emergencia`
- `duty_presenca`, `duty_emerg_ok`, `duty_emerg_travada`, `duty_watchdog`

### Target Columns (Excluded from ML `X`)
- `severity` (`0=NORMAL`, `1=ATENCAO`, `2=CRITICO`, `3=EMERGENCIA`)
- `severity_label` (`NORMAL`, `ATENCAO`, `CRITICO`, `EMERGENCIA`)
- `scenario` (`nominal`, `cadence_drift`, `chattering_b1`, `conveyor_blockage`, `intermittent_high_level`, `sustained_high_level`, `overtemperature`, `watchdog_timeout`, `emergency_stop`, `multi_fault`)
