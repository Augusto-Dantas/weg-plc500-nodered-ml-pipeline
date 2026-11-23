# Pipeline Híbrida de Análise de Dados Industriais — WEG PLC500 + Modbus TCP/IP + Node-RED

> **Idioma / Language:** **Português (Brasil)** | [English](README.md)

Pipeline completa de aquisição de dados industriais, controle determinístico em tecnologia da operação (OT) e análise híbrida com inteligência artificial e aprendizado de máquina integrando o controlador lógico programável **WEG PLC500** (programado em Structured Text IEC 61131-3 via CODESYS), telemetria cíclica **Modbus TCP/IP**, orquestração de borda em **Node-RED**, serviço de inferência em **FastAPI** + **scikit-learn** e persistência histórica em **PostgreSQL**.

---

## Visão Geral da Arquitetura

O sistema separa o intertravamento físico de segurança na camada OT da inferência analítica na camada de TI em três domínios integrados:

1. **Camada OT de Controle Determinístico e Bancada Física (`plc/`, `hardware/`):**
   - **WEG PLC500** executando tarefa cíclica determinística `MainTask` (`10 ms`, prioridade `1`).
   - Segmentação física em duas interfaces Ethernet independentes:
     - **ETH1 (`192.168.20.10/24`)**: Porta exclusiva de engenharia e programação CODESYS.
     - **ETH2 (`192.168.10.10/24`)**: Servidor Modbus TCP dedicado na porta **`502`** (`Unit ID = 1`), consultado pelo host de TI (`192.168.10.20/24`).
   - **Bancada Didática 24 Vcc PNP:**
     - Entradas Digitais: `DI0` (`%IX0.0`, `B1_SensPresenca` NA), `DI1` (`%IX0.1`, `B2_SensNivelAlto` NA), `DI2` (`%IX0.2`, `B3_SensSobretemp` NA) e `DI3` (`%IX0.3`, `B4_Emergencia` com contato **NF** *fail-safe* sem filtro de debounce).
     - Saídas Digitais: `DO0` (`%QX0.0`, `L1_Motor`), `DO1` (`%QX0.1`, `L2_Valvula`), `DO2` (`%QX0.2`, `L3_Alarme`) e `DO3` (`%QX0.3`, `L4_HabRemoto`) com resistores limitadores de corrente de `2.2 kΩ`.
   - Hierarquia estrita de intertravamento local (`FB_SafetyInterlock`):
     1. **Emergência Física (`DI3 = 0` ou falha travada)** -> Corte imediato de `DO0`/`DO1`/`DO3` e acionamento de `DO2` (`uiEstado_CLP = 4`, `uiCod_Intertrav = 1`).
     2. **Sobretemperatura (`DI2 = 1`)** -> Bloqueia motor `DO0`, abre válvula de alívio `DO1` e aciona alarme `DO2` (`uiEstado_CLP = 2`, `uiCod_Intertrav = 2`).
     3. **Estouro de Watchdog Modbus (`xFlag_WatchdogAtivo = TRUE`)** -> Estado seguro desenergizando `DO0`, `DO1` e `DO3` (`uiEstado_CLP = 3`, `uiCod_Intertrav = 3`).
     4. **Bloqueio Manual Local (`xCmd_HabRemoto = FALSE`)** -> Desabilita atuação remota (`uiEstado_CLP = 0`, `uiCod_Intertrav = 4`).
     5. **Operação Remota Normal (`xCmd_HabRemoto = TRUE`)** -> Libera atuação supervisionada (`uiEstado_CLP = 1`, `uiCod_Intertrav = 0`).

2. **Camada de Orquestração Híbrida e Persistência (`node-red/`):**
   - **Node-RED** realiza polling cíclico a cada `500 ms` (`2 Hz`) sobre os 23 itens do mapa Modbus TCP *0-based* (`FC01`, `FC02`, `FC03` e `FC04`) e envia comandos/heartbeats (`FC05`, `FC06`, `FC15` e `FC16`).
   - Agrega janelas deslizantes de `60 s` (`120` amostras), calcula deltas modulares de 16 bits imunes a *wrap-around*, aplica regras determinísticas e z-score, consulta a API FastAPI de ML, valida diagnósticos contra o JSON Schema (`diagnostico_atuacao.schema.json`) e persiste os registros no **PostgreSQL** (`telemetria_sensores`, `janelas_analiticas`, `diagnosticos_ia`, `comandos_atuadores`).

3. **Camada de Aprendizado de Máquina e Gêmeo Digital (`python/`, `data/`):**
   - **Gerador de Dados e Engenharia de Atributos (`plc500_ml`)**: Gera `18 horas` de telemetria industrial a `2 Hz` (`129.600` amostras brutas e `1.080` janelas agregadas de `60 s`).
   - **Modelos Treinados**: Detector de anomalias `IsolationForest` e classificador supervisionado `RandomForestClassifier` em 4 níveis de severidade (`0 = normal`, `1 = atencao`, `2 = alerta`, `3 = critico`) com pós-processamento determinístico de segurança.
   - **Serviço FastAPI (`porta 8000`)**: Expõe os endpoints `/health`, `/model/info`, `/predict` e `/predict/raw-window`.
   - **Simulador Modbus TCP do PLC500 (`plc_simulator.py`)**: Gêmeo digital completo do ciclo de varredura de `10 ms` do WEG PLC500 (`FB_DebounceCounter`, `FB_WatchdogHeartbeat`, `FB_SafetyInterlock`) com API HTTP de injeção de falhas (`porta 8001`) para validação automatizada dos casos de teste `CT-01` a `CT-10`.

---

## Estrutura do Monorepo

| Caminho | Descrição |
| :--- | :--- |
| [`docs/relatorio/`](docs/relatorio/) | Relatório técnico em LaTeX (`main.tex`, `main.pdf` compilado, `referencias.bib` e pacote Overleaf). |
| [`plc/`](plc/) | Código IEC 61131-3 Structured Text do WEG PLC500 (`GVL_Modbus.st`, blocos funcionais, `PLC_PRG.st`), PLCopen TC6 XML v2.01 e tabela CSV do mapa Modbus *0-based*. |
| [`python/`](python/) | Pacote Python `plc500_ml` (gerador sintético, extração de features, treinamento de modelos, API FastAPI, simulador Modbus TCP) e suíte de testes automatizados. |
| [`data/`](data/) | Dataset sintético bruto a `2 Hz` (`data/raw/`), janelas processadas de `60 s` (`data/processed/`) e dicionário de dados. |
| [`node-red/`](node-red/) | Fluxos Node-RED em duas abas (`flows.json`), JSON Schema Draft-07 (`diagnostico_atuacao.schema.json`) e DDL PostgreSQL (`sql/init.sql`). |
| [`hardware/`](hardware/) | Lista de materiais (`bom.csv`), tabela de fiação, fontes LaTeX CircuiTikZ, diagramas compilados (PDF/SVG/PNG) e projeto KiCad 8 (`plc500_bench.kicad_pro`, `plc500_bench.kicad_sch`). |
| [`tests_e2e/`](tests_e2e/) | Suíte de testes end-to-end validando contratos OT, modelos de ML, simulador Modbus TCP (`CT-01..CT-09`), fluxos Node-RED, DDL SQL, KiCad 8 e higiene de repositório. |
| [`docker-compose.yml`](docker-compose.yml) | Orquestração Docker para `postgres`, `ml-api`, `node-red` e `plc-simulator` (perfil `sim`). |
| [`Makefile`](Makefile) | Alvos de automação (`lint`, `test`, `test-e2e`, `ml-generate`, `ml-train`, `sim`, `api`, `diagrams`, `report`, `compose-up`, `compose-sim`, `compose-down`). |

---

## Guia Rápido de Execução

### 1. Configuração de Variáveis de Ambiente

Copie `.env.example` para `.env` e substitua os placeholders `CHANGE_ME_*`:

```bash
cp .env.example .env
```

### 2. Verificação Local, Simulador e Pipeline de ML

Utilizando o ambiente virtual Python 3.12 (`.venv`):

```bash
# Executa o linter Ruff no pacote Python, scripts de hardware e testes E2E
make lint

# Executa testes unitários, ML, FastAPI e simulador Modbus TCP (CT-01..CT-09)
make test

# Executa a suíte completa de testes E2E de validação contratual
make test-e2e

# Regera o dataset sintético e retreina os modelos de ML
make ml-generate
make ml-train

# Inicia o simulador local Modbus TCP do PLC500 (porta padrão não-root 5020) e a API FastAPI (porta 8000)
make sim
make api
```

### 3. Execução via Docker Compose

Para subir toda a pilha com o simulador do WEG PLC500 integrado (perfil `sim`):

```bash
make compose-sim
```

Para operação conectada à bancada física do WEG PLC500 em `192.168.10.10:502`:

```bash
make compose-up
```

### 4. Compilação dos Diagramas de Hardware e Relatório Técnico

```bash
# Compila os esquemáticos elétricos e de rede (PDF, SVG e PNG 300 DPI)
make diagrams

# Compila o PDF do relatório técnico em docs/relatorio/main.pdf
make report
```

---

## Licença

Distribuído sob a licença **MIT**. Consulte o arquivo [`LICENSE`](LICENSE) para mais detalhes.
