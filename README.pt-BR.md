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
     4. **Bloqueio Manual Local (`xCmd_HabRemoto = FALSE`)** -> Desabilita atuação remota (`uiEstado_CLP = 0`, `uiCod_Intertrav = 0`).
     5. **Operação Remota Normal (`xCmd_HabRemoto = TRUE`)** -> Libera atuação supervisionada (`uiEstado_CLP = 1`, `uiCod_Intertrav = 0`).

2. **Camada de Orquestração Híbrida e Persistência (`node-red/`):**
   - **Node-RED** (fluxos organizados em 5 abas em `flows.json`) realiza polling cíclico a cada `500 ms` (`2 Hz`) sobre os 23 itens do mapa Modbus TCP *0-based* (`FC01`, `FC02`, `FC03` e `FC04`) e envia comandos/heartbeats (`FC05`, `FC06`, `FC15` e `FC16`).
   - Agrega janelas deslizantes de `60 s` (`120` amostras, passo de `15 s`), calcula deltas modulares de 16 bits imunes a *wrap-around*, aplica regras determinísticas e z-score, consulta a API FastAPI de ML, valida diagnósticos contra o JSON Schema Draft 2020-12 (`diagnostico_atuacao.schema.json`) e persiste os registros no **PostgreSQL** em 4 tabelas (`telemetria_sensores`, `janelas_analiticas`, `diagnosticos_ia`, `comandos_atuadores`) e 3 visões operacionais (`vw_baseline_24h`, `vw_dashboard_status`, `vw_auditoria_vetos`).

3. **Camada de Aprendizado de Máquina e Gêmeo Digital (`python/`, `data/`):**
   - **Gerador de Dados e Engenharia de Atributos (`plc500_ml`)**: Gera `18 horas` de telemetria industrial a `2 Hz` (`129.600` / `129,600` amostras brutas e `3.688` / `3,688` janelas deslizantes de `60 s` com passo de `15 s` e intervalos de purga `>= 60 s` em particionamento temporal `65/15/20`).
   - **Modelos Treinados**: Detector de anomalias `StandardScaler + IsolationForest` e classificador supervisionado `HistGradientBoostingClassifier` (comparado contra `RandomForestClassifier`) em 4 níveis de severidade (`0 = NORMAL`, `1 = ATENCAO`, `2 = CRITICO`, `3 = EMERGENCIA`) com pós-processamento determinístico de segurança.
   - **Serviço FastAPI (`porta 8000`)**: Expõe os endpoints `/health`, `/schema`, `/model-info`, `/predict` e `/predict/raw-window`.
   - **Simulador Modbus TCP do PLC500 (`plc_simulator.py`)**: Gêmeo digital completo do ciclo de varredura de `10 ms` do WEG PLC500 (`FB_DebounceCounter`, `FB_WatchdogHeartbeat`, `FB_SafetyInterlock`) com API HTTP de injeção de falhas (`porta 8001` no Docker Compose / `.env.example`, ou padrão CLI `8502`) para validação automatizada dos casos de teste `CT-01` a `CT-10`.

---

## Estrutura do Monorepo

| Caminho | Descrição |
| :--- | :--- |
| [`docs/relatorio/`](docs/relatorio/) | Relatório técnico em LaTeX (`main.tex`, PDF compilado `Arquitetura e Especificação Técnica de uma Pipeline Híbrida de Análise de Dados Industriais Integrando WEG PLC500, Comunicação Modbus TCP-IP e Orquestração com Node-RED.pdf`, `referencias.bib` e pacote Overleaf `overleaf_relatorio_abnt_plc500_nodered.zip`). |
| [`plc/`](plc/) | Código IEC 61131-3 Structured Text do WEG PLC500 (`GVL_Modbus.st`, blocos funcionais, `PLC_PRG.st`), PLCopen TC6 XML v2.01 e tabela CSV do mapa Modbus *0-based*. |
| [`python/`](python/) | Pacote Python `plc500_ml` (gerador sintético, extração de features, treinamento de modelos, API FastAPI, simulador Modbus TCP) e suíte de testes automatizados. |
| [`data/`](data/) | Dataset sintético bruto a `2 Hz` (`data/raw/`), janelas processadas de `60 s` (`data/processed/`, `3,688` janelas com passo de `15 s`) e dicionário de dados. |
| [`node-red/`](node-red/) | Fluxos Node-RED em 5 abas (`flows.json`), JSON Schema Draft 2020-12 (`diagnostico_atuacao.schema.json`) e DDL PostgreSQL (`sql/init.sql`, 4 tabelas + 3 visões `vw_baseline_24h`, `vw_dashboard_status`, `vw_auditoria_vetos`). |
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

# Compila o PDF do relatório técnico em docs/relatorio/Arquitetura e Especificação Técnica de uma Pipeline Híbrida de Análise de Dados Industriais Integrando WEG PLC500, Comunicação Modbus TCP-IP e Orquestração com Node-RED.pdf
make report
```

---

## Autores e Identificação Institucional

- **Autores:** **Augusto P. Dantas**, **Gabriel Simão**, **Gabriel Silvano**
- **Coordenador:** **Lucas de Sousa Santos**
- **Instituição:** **Centroweg Senai**
- **Cidade:** **Jaraguá do Sul — SC**

---

## Licença

Distribuído sob a licença **MIT**. Consulte o arquivo [`LICENSE`](LICENSE) para mais detalhes.
