# WEG PLC500 Bench Wiring Table & Pinout Specification

> **IMPORTANT HARDWARE VERIFICATION NOTICE**: The report (`docs/relatorio/`) defines the logical and functional I/O channels (`DI0..DI3`/`%IX0.0..%IX0.3`, `DO0..DO3`/`%QX0.0..%QX0.3`, `+24 Vcc`, `0 Vcc`, `ETH1`, `ETH2`). Before energizing the panel, the installer **must verify the exact physical housing connector/terminal block numbering on the WEG PLC500 silkscreen and official WEG PLC500 User Manual**. All stranded conductors must be crimped with insulated bootlace ferrules (`FERR`).

---

## 1. Power & Protection Distribution (`127/220 Vca` -> `24 Vcc`)

| Circuit / Net | Source Terminal | Intermediate / Protection | Destination Terminal | Gauge | Wire Color (IEC 60204-1) | Notes |
|---|---|---|---|---|---|---|
| `AC_L` | Mains `L` (`127/220 Vca`) | `Q1:1` (IN) -> `Q1:2` (OUT) (`6 A 2P Curve C`) | `PS1:L` (`TB_AC:1`) | `1.5 mm²` | Black | Primary phase protection |
| `AC_N` | Mains `N` (`127/220 Vca`) | `Q1:3` (IN) -> `Q1:4` (OUT) (`6 A 2P Curve C`) | `PS1:N` (`TB_AC:2`) | `1.5 mm²` | Light Blue | Switched neutral |
| `PE` | Mains `PE` (Earth) | `TB_PE:1` | `PS1:PE`, `RAIL1`, `U1:PE` (Chassis) | `1.5 mm²` | Green/Yellow | Equipotential protective earth |
| `24V_RAW` | `PS1:+24V` (`24 Vcc / 2.5 A`) | `F1:1` (IN, `2 A` fuse) | `F1:2` (OUT) -> `TB_24V` bus | `1.5 mm²` | Red | Secondary DC bus protection |
| `+24V` (CPU) | `TB_24V:0` | — | `PLC500:+24Vcc` (CPU Power) | `1.5 mm²` | Red | Regulated `24.0 Vcc ± 0.5 V` |
| `0V` (COM) | `PS1:0V` (COM) | `TB_0V` bus | `PLC500:0Vcc` (CPU & I/O Common) | `1.5 mm²` | Blue | Common reference for PNP I/O |

---

## 2. Digital Inputs (`24 Vcc PNP` Sourcing Field -> PLC Sink)

In PNP (sourcing) configuration, `+24 Vcc` feeds one side of each pushbutton (`TB_24V -> B1..B4`) and the switched `+24 Vcc` returns through `TB_DI` to `PLC500:DI0..DI3` (internally referenced to `0 Vcc`).

| PLC Channel | IEC Addr | Variable (`GVL_Modbus`) | Source (`+24 Vcc`) | Field Device & Contact | Terminal Block | PLC Input Pin | Gauge / Color | Software Filtering & Role |
|---|---|---|---|---|---|---|---|---|
| `DI0` | `%IX0.0` | `xDI_Presenca` | `TB_24V:1` | `B1_SensPresenca` (`B1:3 -> B1:4`, **NA / 1NO**) | `TB_DI:0` | `PLC500:DI0` | `0.75 mm²` White | `TON 50 ms` debounce + `R_TRIG` counter (`IR[0]`) |
| `DI1` | `%IX0.1` | `xDI_NivelAlto` | `TB_24V:2` | `B2_SensNivelAlto` (`B2:3 -> B2:4`, **NA / 1NO**) | `TB_DI:1` | `PLC500:DI1` | `0.75 mm²` White | `TON 50 ms` debounce + `R_TRIG` counter (`IR[1]`) |
| `DI2` | `%IX0.2` | `xDI_Sobretemp` | `TB_24V:3` | `B3_SensSobretemp` (`B3:3 -> B3:4`, **NA / 1NO**) | `TB_DI:2` | `PLC500:DI2` | `0.75 mm²` White | `TON 50 ms` debounce + `R_TRIG` counter (`IR[2]`); vetoes motor |
| `DI3` | `%IX0.3` | `xDI_EmergenciaOK` | `TB_24V:4` | `B4_Emergencia` (`B4:1 -> B4:2`, **NF / 1NC** Mushroom) | `TB_DI:3` | `PLC500:DI3` | `0.75 mm²` Yellow/Orange | **0 ms debounce (Fail-Safe)**: `+24 Vcc` = OK, `0 Vcc` / open wire = Emergency latch (`<= 10 ms`) |
| `DI4..DI7` | `%IX0.4..0.7` | — | — | Spare (No Connect) | — | `PLC500:DI4..DI7` | — | Reserved for future expansion |

---

## 3. Digital Outputs (`24 Vcc PNP` Sourcing PLC -> Load -> `0 Vcc`)

Each PLC500 PNP output sources `+24 Vcc` through a `2.2 kΩ / 0.5 W` current-limiting resistor (`R1..R4`) to the anode (`+`) of a discrete LED (`L1..L4`), returning from the cathode (`-`) to the `TB_0V` bus (`~10 mA` nominal per channel, `0.22 W` resistor dissipation). Alternatively, integrated `24 Vcc` industrial pilot lights may replace the discrete LED + resistor pair directly between `TB_DO:x` and `TB_0V`.

| PLC Channel | IEC Addr | Bit in `IR[5]` (`wMascara_DO_Real`) | PLC Output Pin | Terminal Block | Limiting Resistor | Indicator LED (Polarity) | Return Bus | Gauge / Color | Operational Function |
|---|---|---|---|---|---|---|---|---|---|
| `DO0` | `%QX0.0` | `bit 0` | `PLC500:DO0` | `TB_DO:0` | `R1` (`2.2 kΩ, 0.5 W`) | `L1_AtuMotor` (**Green**, `+` Anode -> `-` Cathode) | `TB_0V:1` | `0.75 mm²` Violet / Blue return | Main motor / conveyor command (vetoed on E-Stop, Overtemp, Watchdog) |
| `DO1` | `%QX0.1` | `bit 1` | `PLC500:DO1` | `TB_DO:1` | `R2` (`2.2 kΩ, 0.5 W`) | `L2_AtuValvula` (**Yellow**, `+` Anode -> `-` Cathode) | `TB_0V:2` | `0.75 mm²` Violet / Blue return | Relief / cooling valve (forced ON on Overtemp or High Level) |
| `DO2` | `%QX0.2` | `bit 2` | `PLC500:DO2` | `TB_DO:2` | `R3` (`2.2 kΩ, 0.5 W`) | `L3_SinalAlarme` (**Red**, `+` Anode -> `-` Cathode) | `TB_0V:3` | `0.75 mm²` Violet / Blue return | Critical alarm (E-Stop, Overtemp, Watchdog, `uiSeveridade_IA >= 2`) |
| `DO3` | `%QX0.3` | `bit 3` | `PLC500:DO3` | `TB_DO:3` | `R4` (`2.2 kΩ, 0.5 W`) | `L4_StatusOper` (**White**, `+` Anode -> `-` Cathode) | `TB_0V:4` | `0.75 mm²` Violet / Blue return | Solid ON when remote supervision mode is healthy (no blinking) |
| `DO4..DO7` | `%QX0.4..0.7` | — | `PLC500:DO4..DO7` | — | — | Spare (No Connect) | — | — | Reserved for future expansion |

---

## 4. Ethernet Segmentation & Firewall ACL (`ETH1` / `ETH2`)

| Interface | Subnet / Mask | PLC IP / Port | Cable (`CAB_ETH`) | Switch / Boundary | Peer Host | Protocol & Firewall Policy |
|---|---|---|---|---|---|---|
| `PLC500:ETH1` | `192.168.20.0/24` (`255.255.255.0`) | `192.168.20.10/24` | Cat6 Blue | `SW_ENG` (Isolated Engineering Switch / VLAN 20) | CODESYS V3.5 Workstation (`192.168.20.50/24`) | CODESYS engineering download, online debug, Trace. Physically/logically isolated from `ETH2` (no inter-VLAN routing). |
| `PLC500:ETH2` | `192.168.10.0/24` (`255.255.255.0`) | `192.168.10.10/24:502` | Cat6 Yellow | `SW_SUP` + Firewall ACL | IT Supervision Host (`192.168.10.20/24`: Node-RED, PostgreSQL, FastAPI) | Modbus TCP Server (`Unit ID 1`, max 4 sockets, 10 s timeout). Polling `Tp = 500 ms`, req timeout `1500 ms`, reconnect `2000 ms`. |

**Mandatory Firewall ACL on `ETH2` Boundary:**
1. `PERMIT TCP src 192.168.10.20/32 dst 192.168.10.10/32 port 502`
2. `DENY TCP src ANY dst 192.168.10.10/32 port 502`
3. Drop/disable IP forwarding between `192.168.20.0/24` (`ETH1`) and `192.168.10.0/24` (`ETH2`).

---

## 5. Safety & Commissioning Checklist

Derived from `docs/relatorio/apendices/apendice_a_checklist.tex` and `capitulos/06_plano_validacao.tex`:

1. **Pre-Power Visual & Continuity Inspection**:
   - Confirm physical terminal numbering on WEG PLC500 silkscreen / manual matches `DI0..DI3`, `DO0..DO3`, `+24 Vcc`, and `0 Vcc`.
   - Confirm `F1` houses a `2 A` fuse and `Q1` is a `6 A` 2P Curve-C MCB.
   - Verify `2.2 kΩ / 0.5 W` resistors (`R1..R4`) are wired in series with `L1..L4` (if discrete LEDs are used) and verify polarity (`+` anode to resistor, `-` cathode to `0 Vcc`).
   - Measure continuity across `B4_Emergencia` (`NF`) from `TB_24V:4` to `TB_DI:3` in released state; confirm open circuit when pressed.
   - Confirm `ETH1` and `ETH2` are patched to separate switches/VLANs with no bypass path.
2. **Power-Up Verification**:
   - Energize `Q1` with `F1` open/removed; measure `PS1` output `24.0 Vcc ± 0.5 V`.
   - De-energize `Q1`, close `F1`, re-energize `Q1`, and confirm PLC500 power LED turns ON.
3. **Field I/O Hardware Verification**:
   - Confirm `DI3` PLC input LED is **ON** (`+24 Vcc`) at rest. Press `B4_Emergencia` and verify `DI3` LED immediately goes **OFF** (`0 Vcc`); twist-release `B4` to restore `+24 Vcc`.
   - Press `B1..B3` individually (`>= 100 ms`) and verify `DI0..DI2` PLC input LEDs illuminate cleanly.
4. **CODESYS Download & Network Verification**:
   - Connect via `ETH1` (`192.168.20.50 -> 192.168.20.10`), verify `ETH2` (`192.168.10.10:502`, `Unit ID 1`), download `PLC_PRG`, switch PLC to `RUN`, and confirm `HR[3] = 100` (`uiVersao_Mapa`).
   - Verify firewall permits `192.168.10.20 -> 192.168.10.10:502` and blocks unauthorized IPs.
5. **Functional & Safety Acceptance (`CT-01..CT-10`)**:
   - Enable remote mode (`Coil[4] = TRUE`), confirm solid ON `L4_StatusOper` (`DO3`).
   - Validate `CT-01` (`< 30 ms` bounce rejection vs `>= 100 ms` pulse count), `CT-07` (`B4` E-Stop shutdown `<= 20 ms`), `CT-08` (`B3` Sobretemp veto `<= 20 ms`), and `CT-09` (`ETH2` cable disconnect watchdog `5.0 s ± 0.2 s`).
