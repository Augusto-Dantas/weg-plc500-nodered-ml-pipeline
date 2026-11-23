# CODESYS V3.5 Setup & Commissioning Guide — WEG PLC500

This guide documents the step-by-step hardware pre-flight check, network segmentation, PLCopen TC6 XML import, `ModbusTCP_Server_Device` I/O mapping, build/download, CODESYS Trace verification, and troubleshooting for the **WEG PLC500** controller (`RT-AUT-2026-001`).

---

## 1. Overview & Prerequisites

### 1.1 Target Hardware
- **Controller**: WEG PLC500 (ARM i.MX Dual Core 1 GHz + 240 MHz coprocessor, 1 GB RAM, 4 GB Flash)
- **Native `Local_IO`**: 8x 24 Vcc PNP Digital Inputs (`%IX0.0..%IX0.3` active), 8x 24 Vcc PNP Digital Outputs (`%QX0.0..%QX0.3` active)
- **Network Interfaces**: Dual independent RJ45 Gigabit Ethernet ports (`ETH1` engineering, `ETH2` Modbus TCP/IP supervision)

### 1.2 Software Prerequisites
- **CODESYS V3.5 IDE** (SP19 or compatible WEG PLC500 engineering release)
- **WEG PLC500 Target / Device Description Package** installed in the CODESYS Device Repository
- **CODESYS Modbus TCP SL** communication package (`ModbusTCP_Server_Device`)

### 1.3 Pre-Flight Electrical Checklist
Before powering or connecting the PLC500 (`docs/relatorio/apendices/apendice_a_checklist.tex`, Etapa 1):
1. Verify regulated **24 Vcc** power supply protected by a bench circuit breaker / fuse.
2. Wire process pushbuttons (`24 Vcc PNP`, Normally Open **NA**) to:
   - `DI0` (`%IX0.0`): `B1_SensPresenca` (part presence sensor simulation)
   - `DI1` (`%IX0.1`): `B2_SensNivelAlto` (reservoir high-level switch simulation)
   - `DI2` (`%IX0.2`): `B3_SensSobretemp` (motor-pump overtemperature thermostat simulation)
3. Wire the latching emergency mushroom pushbutton (`24 Vcc PNP`, **Normally Closed NF fail-safe**) to:
   - `DI3` (`%IX0.3`): `B4_Emergencia` — confirm the PLC input LED for `DI3` is **lit (`TRUE` / +24 Vcc)** in the rest state. An open wire or pressed button (`0 Vcc` / `FALSE`) immediately latches emergency stop on the first `10 ms` scan.
4. Wire the four 24 Vcc PNP outputs (`%QX0.0..%QX0.3`) to 24 Vcc industrial LEDs (or discrete LEDs with `1.8–2.2 kΩ / 0.5 W` current-limiting resistors, nominal `~10 mA`, referenced to `0 Vcc`):
   - `DO0` (`%QX0.0`): `L1_AtuMotor` (Green LED)
   - `DO1` (`%QX0.1`): `L2_AtuValvula` (Yellow LED)
   - `DO2` (`%QX0.2`): `L3_SinalAlarme` (Red LED)
   - `DO3` (`%QX0.3`): `L4_StatusOper` (White LED)
5. Connect separate Ethernet patch cords to `ETH1` (engineering workstation) and `ETH2` (supervisory IT host).

---

## 2. Network Segmentation (`ETH1` & `ETH2`)

| Interface | Role | PLC IP / Mask | Peer IP / Mask | Port & Access Policy |
|---|---|---|---|---|
| `ETH1` | CODESYS Engineering, Download & Trace | `192.168.20.10/24` (`255.255.255.0`) | `192.168.20.50/24` (Engineering PC) | Isolated from supervisory Modbus subnet |
| `ETH2` | Modbus TCP/IP Server (`Ethernet_ETH2`) | `192.168.10.10/24` (`255.255.255.0`) | `192.168.10.20/24` (Node-RED / PostgreSQL / FastAPI Host) | TCP port `502`, Unit ID `1`; firewall restricts TCP/502 exclusively to `192.168.10.20` |

Verify ICMP connectivity from the engineering PC (`192.168.20.50` -> `192.168.20.10`) and from the supervisory host (`192.168.10.20` -> `192.168.10.10:502`).

---

## 3. Importing `plc500_project_plcopen.xml` into CODESYS V3.5

### 3.1 Primary Workflow (PLCopen TC6 XML v2.01 Import)
1. Open CODESYS V3.5 and create a new **Standard Project** targeting the **WEG PLC500** device (`Structured Text (ST)`).
2. In the **Devices** tree, select **Application** (or open `Project -> Import PLCopenXML...`).
3. Browse to `plc/codesys/plc500_project_plcopen.xml`.
4. In the import dialog, select all imported items:
   - `GVL_Modbus` (configured with `{attribute 'qualified_only'}`)
   - `POUs/FB_DebounceCounter`
   - `POUs/FB_WatchdogHeartbeat`
   - `POUs/FB_SafetyInterlock`
   - `POUs/PLC_PRG`
   - `MainTask` (`Cyclic`, `PT0.01S` = `10 ms`, Priority `1`)
5. Choose **Replace existing objects** if a default `PLC_PRG` or `MainTask` already exists in the template.

> **Why hardware device nodes are isolated in `<addData handleUnknown="discard">`**: Different CODESYS/WEG device repository versions use version-locked device GUIDs that trigger *"Device not found"* when imported across installations. Keeping `ModbusTCP_Server_Device` metadata inside `<addData handleUnknown="discard">` guarantees clean POU/GVL/Task import across CODESYS SP versions; configure the device tree nodes per Section 4 below.

### 3.2 Fallback Workflow (Manual `.st` File Import)
If importing individual Structured Text sources instead of the XML bundle:
1. Create a Global Variable List named `GVL_Modbus` and paste `plc/src/GVL_Modbus.st` (preserving `{attribute 'qualified_only'}`).
2. Create Function Blocks `FB_DebounceCounter`, `FB_WatchdogHeartbeat`, and `FB_SafetyInterlock` (all `ST`) from `plc/src/*.st`.
3. Replace `PLC_PRG` with `plc/src/PLC_PRG.st`.
4. Ensure the `Standard` library (`STANDARD.TON`, `STANDARD.R_TRIG`) is present in **Library Manager**.
5. Configure **Task Configuration -> `MainTask`**: Type = `Cyclic`, Interval = `10 ms` (`t#10ms`), Priority = `1`, calling `PLC_PRG`.

---

## 4. Device Tree, `ModbusTCP_Server_Device` & `Local_IO` Mapping

Structure the CODESYS Device Tree in exact hierarchical order (`docs/relatorio/capitulos/04_implementacao_clp.tex` §4.1):

```text
Device (WEG PLC500)
├── PLC Logic
│   └── Application
│       ├── GVL_Modbus
│       ├── POUs (FB_DebounceCounter, FB_WatchdogHeartbeat, FB_SafetyInterlock, PLC_PRG)
│       └── Task Configuration -> MainTask (Cyclic, 10 ms, Priority 1 -> PLC_PRG)
├── Local_IO (%IX0.0..%IX0.3, %QX0.0..%QX0.3)
└── Ethernet_ETH2 (192.168.10.10 / 255.255.255.0)
    └── ModbusTCP_Server_Device (Port 502, Unit ID 1)
```

### 4.1 `Ethernet_ETH2` Configuration
1. Right-click `Device (WEG PLC500)` -> **Add Device...** -> **Ethernet Adapter** (`Ethernet_ETH2`).
2. Bind it to physical interface `ETH2` with static IP `192.168.10.10` and subnet mask `255.255.255.0`.

### 4.2 `ModbusTCP_Server_Device` Configuration
1. Right-click `Ethernet_ETH2` -> **Add Device...** -> **Modbus TCP Slave / ModbusTCP_Server_Device**.
2. Configure **General Parameters**:
   - **Port**: `502`
   - **Unit ID**: `1`
   - **Max Simultaneous Connections**: `4`
   - **Socket Inactivity Timeout**: `10 s`
3. Configure **0-based Register Sizes** (`plc/codesys/modbus_mapping.csv`):
   - **Discrete Inputs (`FC02`)**: `6` bits (addresses `0..5`)
   - **Coils (`FC01/05/15`)**: `5` bits (addresses `0..4`)
   - **Input Registers (`FC04`)**: `8` words (addresses `0..7`)
   - **Holding Registers (`FC03/06/16`)**: `4` words (addresses `0..3`)

### 4.3 `Modbus Server I/O Mapping` & Continuous Variable Update
Map each 0-based channel to its qualified `GVL_Modbus` symbol according to `plc/codesys/modbus_mapping.csv`:

| Area | Addr | GVL Variable | Type | Access |
|---|---|---|---|---|
| Discrete Input (`FC02`) | `0..5` | `xDI_Presenca`, `xDI_NivelAlto`, `xDI_Sobretemp`, `xDI_EmergenciaOK`, `xFlag_EmergTravada`, `xFlag_WatchdogAtivo` | `BOOL` | RO |
| Coil (`FC01/05/15`) | `0..4` | `xCmd_Motor`, `xCmd_Valvula`, `xCmd_Alarme`, `xCmd_ResetFalha`, `xCmd_HabRemoto` | `BOOL` | RW |
| Input Register (`FC04`) | `0..7` | `uiCnt_Presenca`, `uiCnt_NivelAlto`, `uiCnt_Sobretemp`, `uiCnt_Emergencia`, `uiEstado_CLP`, `wMascara_DO_Real`, `uiEcho_Heartbeat`, `uiCod_Intertrav` | `UINT` (`IR[5]` is `WORD`) | RO |
| Holding Register (`FC03/06/16`) | `0..3` | `uiHeartbeat_Sup` *(aliases `uiHeartbeat_NR`/`uiHeartbeat_N8N`)*, `uiTimeout_WD_ms`, `uiSeveridade_IA`, `uiVersao_Mapa` | `UINT` | RW |

> **CRITICAL — Continuous Variable Update (`MainTask`)**:
> In the `ModbusTCP_Server_Device` **I/O Mapping** tab (and `Local_IO` **I/O Mapping** tab), set **Always update variables** to **Enabled 2 (always in bus cycle task)** and bind the bus cycle task to **`MainTask`** (`10 ms`). Without this setting, CODESYS optimizes away unreferenced channel updates and Modbus registers remain stale!

### 4.4 `Local_IO` Hardware Bindings
Because `GVL_Modbus.st` declares `AT %IX0.0..%IX0.3` and `AT %QX0.0..%QX0.3`, confirm `Local_IO` maps `%IX0.0..%IX0.3` and `%QX0.0..%QX0.3` to the native 24 Vcc PNP DI/DO terminals without channel address collisions.

---

## 5. Build, Download, `RUN` & Initial Verification

1. Run **Build -> Generate Code** (`F11`) and verify **0 errors, 0 warnings**.
2. Set the Communication Settings gateway path to `ETH1` (`192.168.20.10`).
3. Click **Online -> Login** (`Alt+F8`), download `Application` (`PLC_PRG`), and switch the controller to **RUN** (`F5`).
4. In Online Monitoring (or via Modbus `FC03` from `192.168.10.20`), verify:
   - `GVL_Modbus.uiVersao_Mapa` (`HR[3]`) == `100`
   - `GVL_Modbus.uiTimeout_WD_ms` (`HR[1]`) == `5000`
   - `GVL_Modbus.xDI_EmergenciaOK` (`DI[3]`) == `TRUE` (with `B4_Emergencia` NF closed)
   - `GVL_Modbus.uiEstado_CLP` (`IR[4]`) == `0` (`INIT`)
5. **Supervisory Enable Smoke Test**:
   - Write `HR[0]` (`uiHeartbeat_Sup`) incrementing `1, 2, 3, ...` every `500 ms` and write `Coil[4]` (`xCmd_HabRemoto`) = `TRUE`.
   - Confirm `uiEstado_CLP` (`IR[4]`) transitions to `1` (`NORMAL_AUTO`), `uiEcho_Heartbeat` (`IR[6]`) mirrors `HR[0]`, and physical `DO3` (`L4_StatusOper`, White LED) lights solid ON (`wMascara_DO_Real = 8`).

> **Note on `Coil[3]` (`xCmd_ResetFalha`)**: `FB_SafetyInterlock` evaluates `STANDARD.R_TRIG` on `xCmd_ResetFalha` without mutating the Coil from PLC logic. Supervisory clients must write `Coil[3] := TRUE` and return `Coil[3] := FALSE` after reset (rising-edge pulse), and reset succeeds only when `DI3 = 1` (`xDI_EmergenciaOK = TRUE`) AND `DI2 = 0` (`xDI_Sobretemp = FALSE`).

---

## 6. CODESYS Trace & Bench Verification (`CT-01` .. `CT-09`)

Add a **Trace** object under `Application` assigned to `MainTask` (`10 ms` sample period) recording:
- `GVL_Modbus.xRaw_DI0_Presenca`, `GVL_Modbus.xRaw_DI2_Sobretemp`, `GVL_Modbus.xRaw_DI3_Emergencia`
- `GVL_Modbus.xDI_Presenca`, `GVL_Modbus.xDI_Sobretemp`, `GVL_Modbus.xDI_EmergenciaOK`
- `GVL_Modbus.xFlag_EmergTravada`, `GVL_Modbus.xFlag_WatchdogAtivo`
- `GVL_Modbus.uiEstado_CLP`, `GVL_Modbus.uiCod_Intertrav`, `GVL_Modbus.wMascara_DO_Real`

Recommended commissioning checks (`docs/relatorio/capitulos/06_plano_validacao.tex`):
- **`CT-01` (Debounce)**: `< 30 ms` pulses on `DI0..DI2` must be rejected; `>= 100 ms` pulses increment `IR[0..2]` by `+1` after `50 ms` (`5` scans).
- **`CT-02` (Remote Actuation)**: `FC15` writes to `Coils 0..2` update `DO0..DO2` and `wMascara_DO_Real` (`IR[5]`).
- **`CT-07` (Emergency Stop `DI3`)**: Opening `B4_Emergencia` (`DI3 = FALSE`) must drive `DO0=0, DO1=0, DO2=1, DO3=0` (`wMascara_DO_Real = 4`), `uiEstado_CLP = 4`, `uiCod_Intertrav = 1` within `<= 20 ms` (`<= 2` scans on Trace). Verify `Coil[3]` reset is rejected while `DI3=0` or `DI2=1`.
- **`CT-08` (Overtemperature `DI2`)**: Activating `B3_SensSobretemp` (`DI2 = TRUE`) while `Coil[0]=1` must force `DO0=0, DO1=1, DO2=1`, `uiEstado_CLP = 2`, `uiCod_Intertrav = 2` within `<= 20 ms`.
- **`CT-09` (Watchdog `ETH2`)**: Stopping `HR[0]` updates must trigger `xFlag_WatchdogAtivo = TRUE`, `uiEstado_CLP = 3`, `uiCod_Intertrav = 3`, `DO0=0, DO2=1, DO3=0, DO1=xDI_NivelAlto` in `5.0 s ± 0.2 s` (`500` scans at default `HR[1] = 5000`).

---

## 7. Troubleshooting

| Symptom | Root Cause | Resolution |
|---|---|---|
| *"Device not found"* during XML import | Target CODESYS/WEG Device Repository version differs | Import POUs/GVL/`MainTask` from `plc500_project_plcopen.xml` (hardware stays in `handleUnknown="discard"`) and add `Ethernet_ETH2` + `ModbusTCP_Server_Device` manually per Section 4. |
| `HR[3]` (`uiVersao_Mapa`) reads `0` instead of `100` | PLC is in `STOP` or `PLC_PRG` is not bound to `MainTask` | Verify `MainTask` calls `PLC_PRG`, download, and switch controller to `RUN`. |
| Modbus registers do not update in `RUN` | Continuous variable update disabled on I/O Mapping | Enable **Always update variables -> Enabled 2 (always in bus cycle task)** in `ModbusTCP_Server_Device` and `Local_IO`, linked to `MainTask`. |
| Immediate `EMERGENCIA_TRAV` (`uiEstado_CLP = 4`) on cold boot | `DI3` (`%IX0.3`) NF circuit is open or missing `+24 Vcc` | Check `B4_Emergencia` NC contact, `+24 Vcc` supply, and `DI3` input LED; once `DI3 = TRUE` and `DI2 = FALSE`, pulse `Coil[3]` (`FALSE -> TRUE -> FALSE`). |
| Reset pulse `Coil[3]` does not clear `xFlag_EmergTravada` | `DI3=0`, `DI2=1`, or `Coil[3]` was already `TRUE` (no rising edge) | Confirm `xDI_EmergenciaOK = TRUE` and `xDI_Sobretemp = FALSE`, then cycle `Coil[3]` `FALSE -> TRUE`. |
| Watchdog activates immediately after `5 s` even while client sends `HR[0]` | Client writes constant value (`HR[0]` not incrementing) or wrong register offset | Increment `HR[0]` (`1..65535`) every `500 ms` using **0-based** address `0`. |
