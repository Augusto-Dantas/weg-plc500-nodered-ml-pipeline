# ==============================================================================
# Makefile — weg-plc500-nodered-ml-pipeline
# ==============================================================================

export PATH := $(HOME)/.local/bin:$(HOME)/.TinyTeX/bin/x86_64-linux:$(CURDIR)/.venv/bin:$(PATH)

VENV_BIN := $(CURDIR)/.venv/bin
PYTHON   := $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python,python3)
PYTEST   := $(if $(wildcard $(VENV_BIN)/pytest),$(VENV_BIN)/pytest,pytest)
RUFF     := $(if $(wildcard $(VENV_BIN)/ruff),$(VENV_BIN)/ruff,ruff)
UVICORN  := $(if $(wildcard $(VENV_BIN)/uvicorn),$(VENV_BIN)/uvicorn,uvicorn)

.DEFAULT_GOAL := help

.PHONY: help lint test test-e2e ml-generate ml-train sim api diagrams report compose-up compose-sim compose-down

help: ## Show available developer targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

lint: ## Run ruff linter across Python code, scripts, and tests
	$(RUFF) check $(or $(wildcard python hardware/scripts tests_e2e),.)

test: ## Run Python unit, ML, FastAPI, and Modbus CT-01..CT-09 tests
	PYTHONPATH=$(CURDIR)/python $(PYTEST) python/tests -v

test-e2e: ## Run end-to-end validation test suite
	PYTHONPATH=$(CURDIR)/python $(PYTEST) tests_e2e -v

ml-generate: ## Generate synthetic 18h @ 2Hz telemetry dataset and 60s windows
	PYTHONPATH=$(CURDIR)/python $(PYTHON) -m plc500_ml.data_generator

ml-train: ## Train anomaly detector and severity classifier models
	PYTHONPATH=$(CURDIR)/python $(PYTHON) -m plc500_ml.train

sim: ## Start local Modbus TCP PLC500 simulator (non-root default port 5020)
	PYTHONPATH=$(CURDIR)/python PLC_SIM_PORT=$${PLC_SIM_PORT:-5020} $(PYTHON) -m plc500_ml.plc_simulator

api: ## Start FastAPI ML inference service on port 8000
	PYTHONPATH=$(CURDIR)/python $(UVICORN) plc500_ml.api:app --host $${ML_API_HOST:-0.0.0.0} --port $${ML_API_PORT:-8000}

diagrams: ## Render hardware CircuiTikZ schematics (PDF, SVG, 300 DPI PNG)
	$(PYTHON) hardware/scripts/generate_schematics.py

report: ## Compile ABNT LaTeX technical report PDF via docs/relatorio/Makefile
	$(MAKE) -C docs/relatorio pdf

compose-up: ## Build and start PostgreSQL, ML API, and Node-RED stack
	docker compose up -d --build

compose-sim: ## Build and start full stack including simulated PLC500 profile
	PLC_HOST=plc-simulator docker compose --profile sim up -d --build

compose-down: ## Stop all Docker Compose services (including sim profile)
	docker compose --profile sim down
