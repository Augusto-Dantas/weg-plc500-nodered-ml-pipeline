# `plc500-ml` — Python ML, Feature Engineering, Safety Rules & Modbus TCP Simulator

Python analytical, machine learning, FastAPI inference, and OT-faithful Modbus TCP simulation layer for the **WEG PLC500 + Node-RED + PostgreSQL** industrial supervisory & safety interlock system (`RT-AUT-2026-001`).

## Package Structure

- `plc500_ml/config.py`: Canonical OT/IT constants, 0-based Modbus TCP register maps, FSM states, interlock codes, windowing defaults (`W=60s`, `Tp=0.5s`, `K=120`, `stride=15s`), and baseline parameters (`mu=12.0`, `sigma=2.5`, `eps=0.01`).
- `plc500_ml/schemas.py`: Exact JSON Schema Draft 2020-12 `DiagnosticoAtuacaoPLC500` (`additionalProperties: false`), strict Pydantic v2 contracts, and FastAPI request/response models.
- `plc500_ml/features.py`: `deltaMod16`, arrival rate `lambda1`, duty cycles `duty_nivel` / `duty_sobretemp`, regularized Z-score, bounded `chattering_index`, local veto flags, and the shared 10-feature `MODEL_FEATURE_COLUMNS` vector (zero train/serve skew).
- `plc500_ml/safety_rules.py`: Deterministic statistical fallback (`0..3`) and Monotonicity Safety Guardian (blocks severity downgrades and unsafe `cmd_motor=True` under overtemp, sustained high level, watchdog, or emergency).
- `plc500_ml/data_generator.py`: Reproducible (`seed=42`) 18h @ 2 Hz (`129,600` samples) synthetic telemetry generator across 9 physical/OT fault scenarios + `multi_fault`, with strict temporal `65/15/20` split and `>= 60 s` purge gaps.
- `plc500_ml/train.py`: Leakage-verified ML pipeline (`StandardScaler + IsolationForest` fitted strictly on normal training windows, `HistGradientBoostingClassifier` vs `RandomForestClassifier` benchmarked on `val` and evaluated on `test`).
- `plc500_ml/inference.py`: `PLC500InferenceEngine` combining anomaly detection, severity classification, monotonicity safety guard, JSON Schema validation, and graceful deterministic fallback when model artifacts are absent.
- `plc500_ml/api.py`: FastAPI service exposing `GET /health`, `GET /schema`, `GET /model-info`, `POST /predict`, and `POST /predict/raw-window`.
- `plc500_ml/plc_simulator.py`: 10 ms scan-cycle `pymodbus` TCP server + fault-injection control API matching `PLC_PRG` Structured Text (`CT-01..CT-09`).

## Setup (via `uv` & `.venv`)

```bash
# From repository root (/home/augusto_dantas/sla)
~/.local/bin/uv pip install --python .venv/bin/python -r python/requirements.txt
~/.local/bin/uv pip install --python .venv/bin/python -e ./python
```

## Common Commands

```bash
# 1. Generate 18h @ 2Hz synthetic dataset and 60s sliding windows
.venv/bin/python -m plc500_ml.data_generator

# 2. Train & evaluate anomaly detector + severity classifier
.venv/bin/python -m plc500_ml.train

# 3. Start FastAPI inference service (port 8000)
.venv/bin/uvicorn plc500_ml.api:app --host 0.0.0.0 --port 8000

# 4. Start Modbus TCP PLC simulator locally without root (port 5020 + control API 8502)
.venv/bin/python -m plc500_ml.plc_simulator --host 0.0.0.0 --modbus-port 5020 --control-port 8502

# 5. Lint & run tests
.venv/bin/ruff check python/
.venv/bin/pytest python/tests/ -v
```
