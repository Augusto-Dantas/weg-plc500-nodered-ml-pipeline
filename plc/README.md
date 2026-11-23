# OT Control Layer — WEG PLC500 (`plc/`)

Deterministic **IEC 61131-3 Structured Text** control layer and **PLCopen TC6 XML v2.01** project for the **WEG PLC500** running **CODESYS V3.5** (`MainTask`, `10 ms` cyclic scan), decoupled from the IT supervisory stack (Node-RED / FastAPI ML / PostgreSQL).

---

## 1. Directory Layout

```text
plc/
├── README.md                              # OT module contract & architecture overview
├── docs/
│   └── codesys_setup_guide.md             # Step-by-step CODESYS V3.5 & commissioning guide
├── codesys/
│   ├── modbus_mapping.csv                 # Authoritative 23-register 0-based Modbus TCP contract
│   └── plc500_project_plcopen.xml         # PLCopen TC6 XML v2.01 importable project bundle
└── src/
    ├── GVL_Modbus.st                      # Qualified-only GVL: %IX/%QX bindings + 23 Modbus symbols
    ├── FB_DebounceCounter.st              # 50 ms TON debounce (DI0..DI2), NF bypass (DI3), mod-16 R_TRIG counter
    ├── FB_WatchdogHeartbeat.st            # HR[1] [2000..30000] guard & pre-update TON heartbeat monitor
    ├── FB_SafetyInterlock.st              # R_TRIG safe reset, 5-level IF/ELSIF FSM, wMascara_DO_Real
    └── PLC_PRG.st                         # Deterministic 10 ms cyclic scan orchestrator
```

---

## 2. Modular Structured Text Architecture (`plc/src/`)

| File | IEC POU Type | Responsibility |
|---|---|---|
| `GVL_Modbus.st` | `VAR_GLOBAL` (`{attribute 'qualified_only'}`) | Centralizes `Local_IO` bindings (`%IX0.0..%IX0.3`, `%QX0.0..%QX0.3`) and all 23 Modbus TCP registers (`DI 0..5`, `Coils 0..4`, `IR 0..7`, `HR 0..3`). |
| `FB_DebounceCounter.st` | `FUNCTION_BLOCK` | Applies `STANDARD.TON` (`T#50MS`) when `xEnableDebounce=TRUE` (`DI0..DI2`), zero-delay pass-through when `FALSE` (`DI3` NF), `STANDARD.R_TRIG` edge detection (`xCountOnFalling`), and natural `UINT` modular `+1` wrap (`65535 -> 0`). |
| `FB_WatchdogHeartbeat.st` | `FUNCTION_BLOCK` | Clamps `uiTimeoutMs` to `[2000..30000]` ms (fallback `T#5S`). **Critical invariant**: evaluates `tonWatchdog(IN := (uiHeartbeat = uiLastHeartbeat), PT := tActiveLimit)` **before** updating `uiLastHeartbeat` and `uiEchoHeartbeat`. |
| `FB_SafetyInterlock.st` | `FUNCTION_BLOCK` | Evaluates `STANDARD.R_TRIG` on `xCmd_ResetFalha` every scan without mutating Modbus `Coil[3]`, latches `xFlag_EmergTravada` on `NOT xDI_EmergenciaOK`, enforces strict 5-branch `IF/ELSIF` precedence, and builds `wMascara_DO_Real`. |
| `PLC_PRG.st` | `PROGRAM` (`MainTask` `10 ms`) | Executes Steps 1–6 sequentially every `10 ms` and unconditionally assigns `GVL_Modbus.uiVersao_Mapa := 100;`. |

### 10 ms Cyclic Scan Order (`PLC_PRG.st`)
1. **Inputs & Debounce (`DI0..DI3`)**: 4 `FB_DebounceCounter` calls (`TON 50 ms` on `DI0..DI2`; immediate NF on `DI3`, counting on falling edge `NOT xDI_EmergenciaOK`).
2. **Modular Counters (`IR[0..3]`)**: `UINT` rising/falling-edge accumulation with natural 16-bit rollover (`65535 -> 0`).
3. **Link Watchdog (`HR[0..1]`, `IR[6]`, `DI[5]`)**: `FB_WatchdogHeartbeat` validates `uiTimeout_WD_ms`, runs `tonWatchdog` before updating `uiLastHeartbeat`/`uiEcho_Heartbeat`, and outputs `xFlag_WatchdogAtivo`.
4. **Emergency Latch & Safe Reset**: `NOT xDI_EmergenciaOK` latches `xFlag_EmergTravada`; cleared only on `R_TRIG(xCmd_ResetFalha)` AND `xDI_EmergenciaOK` AND `NOT xDI_Sobretemp`.
5. **Strict Safety FSM (`IR[4]`, `IR[7]`, `%QX0.0..%QX0.3`)**: 5-branch priority evaluation.
6. **Output Bitmask & Contract Version (`IR[5]`, `HR[3]`)**: Packs `wMascara_DO_Real` (`bit0=DO0..bit3=DO3`) and forces `GVL_Modbus.uiVersao_Mapa := 100;`.

---

## 3. Hardware I/O & Electrical Contract (`24 Vcc PNP`)

| Channel | IEC Addr | Identifier | Contact / Load | Debounce | Behavior |
|---|---|---|---|---|---|
| `DI0` | `%IX0.0` | `B1_SensPresenca` | NA (24 Vcc PNP) | `TON 50 ms` | Part presence sensor on conveyor (cycle count) |
| `DI1` | `%IX0.1` | `B2_SensNivelAlto` | NA (24 Vcc PNP) | `TON 50 ms` | Reservoir high-level switch (forces `DO1` in AUTO/DEGRADED) |
| `DI2` | `%IX0.2` | `B3_SensSobretemp` | NA (24 Vcc PNP) | `TON 50 ms` | Motor-pump overtemperature thermostat (vetoes `DO0`, forces `DO1=1, DO2=1`) |
| `DI3` | `%IX0.3` | `B4_Emergencia` | **NF (NC) fail-safe** | **None (`0 ms`)** | `TRUE` (+24 Vcc) at rest; `FALSE` (0 Vcc / wire break) latches E-stop in `<= 10 ms` |
| `DO0` | `%QX0.0` | `L1_AtuMotor` | Green LED (24 Vcc) | — | Motor/conveyor actuator (vetoed by E-stop, Overtemp, Watchdog) |
| `DO1` | `%QX0.1` | `L2_AtuValvula` | Yellow LED (24 Vcc) | — | Cooling / relief valve (`OR`ed locally with `xDI_NivelAlto` and `xDI_Sobretemp`) |
| `DO2` | `%QX0.2` | `L3_SinalAlarme` | Red LED (24 Vcc) | — | Alarm beacon (`TRUE` on E-stop, Overtemp, Watchdog, `uiSeveridade_IA >= 2`, or `xCmd_Alarme`) |
| `DO3` | `%QX0.3` | `L4_StatusOper` | White LED (24 Vcc) | — | Solid `TRUE` only when no E-stop, watchdog healthy, and `xCmd_HabRemoto = TRUE` |

*(Discrete LEDs use `1.8–2.2 kΩ / 0.5 W` current-limiting resistors to `0 Vcc`, ~`10 mA` nominal.)*

---

## 4. Network & Zero-Based Modbus TCP Contract

- **Engineering (`ETH1`)**: `192.168.20.10/24` <-> CODESYS Workstation `192.168.20.50/24`
- **Supervision (`ETH2`)**: `192.168.10.10/24:502` (`Unit ID 1`, max `4` connections, `10 s` socket timeout) <-> IT Host `192.168.10.20/24` (firewall TCP/502 restricted to `.20`, polling `Tp = 500 ms`)

Full 23-register contract: [`plc/codesys/modbus_mapping.csv`](codesys/modbus_mapping.csv).

- **Discrete Inputs (`FC02`, `0..5`)**: `xDI_Presenca`, `xDI_NivelAlto`, `xDI_Sobretemp`, `xDI_EmergenciaOK` (`default=TRUE`), `xFlag_EmergTravada`, `xFlag_WatchdogAtivo`
- **Coils (`FC01/05/15`, `0..4`)**: `xCmd_Motor`, `xCmd_Valvula`, `xCmd_Alarme`, `xCmd_ResetFalha` (`R_TRIG` pulse), `xCmd_HabRemoto`
- **Input Registers (`FC04`, `0..7`)**: `uiCnt_Presenca`, `uiCnt_NivelAlto`, `uiCnt_Sobretemp`, `uiCnt_Emergencia`, `uiEstado_CLP` (`0..4`), `wMascara_DO_Real` (`WORD`), `uiEcho_Heartbeat`, `uiCod_Intertrav` (`0..3`)
- **Holding Registers (`FC03/06/16`, `0..3`)**: `uiHeartbeat_Sup` *(compatible aliases `uiHeartbeat_NR`, `uiHeartbeat_N8N`)*, `uiTimeout_WD_ms` (`[2000..30000]`, default `5000`), `uiSeveridade_IA` (`0..3`), `uiVersao_Mapa` (`100`)

---

## 5. Finite State Machine & Safety Precedence

Strict `IF / ELSIF` precedence (`xFlag_EmergTravada > xDI_Sobretemp > xFlag_WatchdogAtivo > xCmd_HabRemoto > INIT`):

| Priority | Condition | `uiEstado_CLP` (`IR[4]`) | `uiCod_Intertrav` (`IR[7]`) | `DO0` (`L1`) | `DO1` (`L2`) | `DO2` (`L3`) | `DO3` (`L4`) |
|---|---|---|---|---|---|---|---|
| 1 | `xFlag_EmergTravada = TRUE` | `4` (`EMERGENCIA_TRAV`) | `1` (`Emergencia`) | `0` | `0` | `1` | `0` |
| 2 | `xDI_Sobretemp = TRUE` | `2` (`ALERTA`) | `2` (`Sobretemp`) | `0` | `1` | `1` | `(NOT WD) AND HabRemoto` |
| 3 | `xFlag_WatchdogAtivo = TRUE` | `3` (`MODO_DEGRADADO`) | `3` (`Watchdog`) | `0` | `xDI_NivelAlto` | `1` | `0` |
| 4 | `xCmd_HabRemoto = TRUE` | `1` (`NORMAL_AUTO`) or `2` (`ALERTA` if `Sev_IA>0 OR DI1`) | `0` (`Nenhum`) | `xCmd_Motor` | `xCmd_Valvula OR DI1` | `xCmd_Alarme OR (Sev_IA>=2)` | `1` |
| 5 | `ELSE` (`INIT`) | `0` (`INIT`) | `0` (`Nenhum`) | `0` | `0` | `0` | `0` |

### Key Safety & Contract Invariants
- **Watchdog Evaluation Order**: `tonWatchdog(IN := (uiHeartbeat = uiLastHeartbeat), PT := tActiveLimit)` runs **before** updating `uiLastHeartbeat` and `uiEchoHeartbeat`, ensuring any heartbeat change resets `IN := FALSE` for one `10 ms` scan cycle.
- **Safe Reset Guard**: `xFlag_EmergTravada` clears **only** on a rising edge (`R_TRIG`) of `xCmd_ResetFalha` while `xDI_EmergenciaOK = TRUE` AND `xDI_Sobretemp = FALSE`.
- **Overtemperature `DO3` Clarification**: `xDO3_StatusOper := (NOT xFlag_WatchdogAtivo) AND xCmd_HabRemoto` aligns `docs/relatorio/capitulos/04_implementacao_clp.tex` (line 141 vs. prose at line 165), ensuring `L4_StatusOper` never illuminates when remote supervision is disabled (`INIT`).

---

## 6. CODESYS Import & Local Validation

- See [`plc/docs/codesys_setup_guide.md`](docs/codesys_setup_guide.md) for CODESYS V3.5 XML import, `ModbusTCP_Server_Device` setup, and hardware commissioning.
- Validate the PLCopen TC6 v2.01 XML well-formedness and structure with Python stdlib:

```bash
.venv/bin/python -c "import xml.etree.ElementTree as ET; root = ET.parse('plc/codesys/plc500_project_plcopen.xml').getroot(); print(root.tag)"
```

- For software-in-the-loop Modbus TCP validation (`CT-01..CT-09`), refer to the Python simulator (`python/plc500_ml/plc_simulator.py`) and test suite (`python/tests/`).
