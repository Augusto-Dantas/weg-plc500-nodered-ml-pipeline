# WEG PLC500 + Modbus TCP/IP + Node-RED Hybrid Industrial Data Analysis Pipeline

> **Language / Idioma:** **English** | [Português (Brasil)](README.pt-BR.md)

An end-to-end industrial data acquisition, deterministic OT control, and hybrid AI/ML analytics pipeline integrating the **WEG PLC500** programmable logic controller (programmed in IEC 61131-3 Structured Text via CODESYS), **Modbus TCP/IP** cyclic telemetry, **Node-RED** edge orchestration, **FastAPI** + **scikit-learn** machine learning inference, and **PostgreSQL** historical persistence.

---

## Architecture Overview

The system separates physical safety-critical OT interlocks from IT analytics across three layers:

1. **OT Control & Physical Bench Layer (`plc/`, `hardware/`):**
   - **WEG PLC500** running a deterministic `MainTask` cycle (`10 ms`, priority `1`).
   - Dual Ethernet segmentation:
     - **ETH1 (`192.168.20.10/24`)**: Engineering & CODESYS programming port.
     - **ETH2 (`192.168.10.10/24`)**: Dedicated Modbus TCP Server on port **`502`** (`Unit ID = 1`), polled by the IT host (`192.168.10.20/24`).
   - **24 Vcc PNP Bench I/O:**
     - Digital Inputs: `DI0` (`%IX0.0`, `B1_SensPresenca` NA), `DI1` (`%IX0.1`, `B2_SensNivelAlto` NA), `DI2` (`%IX0.2`, `B3_SensSobretemp` NA), and `DI3` (`%IX0.3`, `B4_Emergencia` fail-safe **NF** contact without debounce).
     - Digital Outputs: `DO0` (`%QX0.0`, `L1_Motor`), `DO1` (`%QX0.1`, `L2_Valvula`), `DO2` (`%QX0.2`, `L3_Alarme`), and `DO3` (`%QX0.3`, `L4_HabRemoto`) with `2.2 kΩ` current-limiting resistors.
   - Hardware-enforced safety interlock precedence (`FB_SafetyInterlock`):
     1. **Emergency Stop (`DI3 = 0` or latched fault)** -> Immediate cut-off of `DO0`/`DO1`/`DO3` and activation of alarm `DO2` (`uiEstado_CLP = 4`, `uiCod_Intertrav = 1`).
     2. **Overtemperature (`DI2 = 1`)** -> Blocks motor `DO0`, opens relief valve `DO1`, activates alarm `DO2` (`uiEstado_CLP = 2`, `uiCod_Intertrav = 2`).
     3. **Modbus Watchdog Timeout (`xFlag_WatchdogAtivo = TRUE`)** -> Safe state de-energizing `DO0`, `DO1`, and `DO3` (`uiEstado_CLP = 3`, `uiCod_Intertrav = 3`).
     4. **Local Manual Override (`xCmd_HabRemoto = FALSE`)** -> Disables remote actuation (`uiEstado_CLP = 0`, `uiCod_Intertrav = 4`).
     5. **Remote Normal Operation (`xCmd_HabRemoto = TRUE`)** -> Grants supervisory command authority (`uiEstado_CLP = 1`, `uiCod_Intertrav = 0`).

2. **Hybrid Edge Orchestration & Persistence Layer (`node-red/`):**
   - **Node-RED** polls the 23-variable 0-based Modbus TCP register map every `500 ms` (`2 Hz`) via `FC01`, `FC02`, `FC03`, and `FC04`, and writes supervisory commands/heartbeats via `FC05`, `FC06`, `FC15`, and `FC16`.
   - Aggregates `60 s` sliding telemetry windows (`120` samples per window), computes 16-bit wrap-safe modular counter deltas, evaluates deterministic z-score and safety rules, queries the FastAPI ML inference service, validates output payloads against JSON Schema (`diagnostico_atuacao.schema.json`), and logs records into **PostgreSQL** (`telemetria_sensores`, `janelas_analiticas`, `diagnosticos_ia`, `comandos_atuadores`).

3. **Machine Learning & Digital Twin Simulation Layer (`python/`, `data/`):**
   - **Synthetic Data Generator & Feature Pipeline (`plc500_ml`)**: Generates `18 hours` of `2 Hz` industrial telemetry (`129,600` raw samples and `1,080` aggregated `60 s` windows) covering nominal, degradation, jam, chattering, level-alarm, overtemperature, emergency, and watchdog scenarios.
   - **Trained Models**: `IsolationForest` anomaly detector and `RandomForestClassifier` 4-class severity classifier (`0 = normal`, `1 = atencao`, `2 = alerta`, `3 = critico`) with deterministic safety post-processing.
   - **FastAPI Service (`port 8000`)**: Exposes `/health`, `/model/info`, `/predict`, and `/predict/raw-window`.
   - **Stateful Modbus TCP PLC500 Simulator (`plc_simulator.py`)**: Full digital twin of the WEG PLC500 `10 ms` scan cycle (`FB_DebounceCounter`, `FB_WatchdogHeartbeat`, `FB_SafetyInterlock`) plus a local HTTP fault-injection API (`port 8001`) for automated validation of test cases `CT-01` through `CT-10`.

---

## Monorepo Directory Layout

| Path | Description |
| :--- | :--- |
| [`docs/relatorio/`](docs/relatorio/) | Technical report in LaTeX (`main.tex`, compiled `main.pdf`, `referencias.bib`, and Overleaf package). |
| [`plc/`](plc/) | WEG PLC500 IEC 61131-3 Structured Text (`GVL_Modbus.st`, function blocks, `PLC_PRG.st`), PLCopen TC6 XML v2.01, and 0-based Modbus CSV mapping. |
| [`python/`](python/) | Python package `plc500_ml` (data generator, feature engineering, model training, FastAPI service, Modbus TCP PLC simulator) and unit/integration test suite. |
| [`data/`](data/) | Synthetic raw `2 Hz` dataset (`data/raw/`), processed `60 s` feature windows (`data/processed/`), and data dictionary. |
| [`node-red/`](node-red/) | Node-RED two-tab flows (`flows.json`), JSON Schema Draft-07 (`diagnostico_atuacao.schema.json`), and PostgreSQL DDL (`sql/init.sql`). |
| [`hardware/`](hardware/) | Bench bill of materials (`bom.csv`), wiring table, CircuiTikZ LaTeX schematics, compiled PDF/SVG/PNG diagrams, and KiCad 8 project (`plc500_bench.kicad_pro`, `plc500_bench.kicad_sch`). |
| [`tests_e2e/`](tests_e2e/) | End-to-end validation test suite verifying OT contracts, ML models, Modbus TCP simulator (`CT-01..CT-09`), Node-RED flows, SQL DDL, KiCad 8, and repository hygiene. |
| [`docker-compose.yml`](docker-compose.yml) | Container orchestration for `postgres`, `ml-api`, `node-red`, and optional `plc-simulator` (`sim` profile). |
| [`Makefile`](Makefile) | Developer automation targets (`lint`, `test`, `test-e2e`, `ml-generate`, `ml-train`, `sim`, `api`, `diagrams`, `report`, `compose-up`, `compose-sim`, `compose-down`). |

---

## Quick Start

### 1. Environment Configuration

Copy `.env.example` to `.env` and replace the `CHANGE_ME_*` placeholders before launching services:

```bash
cp .env.example .env
```

### 2. Local Python Verification, Simulation & ML Pipeline

Using the local Python 3.12 virtual environment (`.venv`):

```bash
# Run Ruff linter across Python package, hardware scripts, and E2E tests
make lint

# Run unit, ML, FastAPI, and Modbus TCP simulator tests (CT-01..CT-09)
make test

# Run full E2E contract validation test suite
make test-e2e

# Regenerate synthetic telemetry dataset and retrain ML models
make ml-generate
make ml-train

# Start the local PLC500 Modbus TCP simulator (default non-root port 5020) and FastAPI server (port 8000)
make sim
make api
```

### 3. Docker Compose Full-Stack Execution

To start PostgreSQL, the FastAPI ML inference service, Node-RED, and the simulated WEG PLC500 (`sim` profile):

```bash
make compose-sim
```

When connected to a physical WEG PLC500 bench at `192.168.10.10:502`:

```bash
make compose-up
```

### 4. Rebuilding Hardware Diagrams & Technical Report

```bash
# Render electrical wiring and network layout diagrams (PDF, SVG, 300 DPI PNG)
make diagrams

# Compile the technical report PDF in docs/relatorio/main.pdf
make report
```

---

## License

Released under the **MIT License**. See [`LICENSE`](LICENSE) for details.
