# Hardware Bench, Electrical Schematics & KiCad Project (`hardware/`)

This directory contains the complete hardware engineering documentation, Bill of Materials (BOM), terminal-to-terminal wiring specification, CircuiTikZ/TikZ schematics (and generated PDF/SVG/300 DPI PNG diagrams), and KiCad 8 schematic project for the **WEG PLC500 OT/IT Industrial Supervision & Safety Interlock Bench** (`RT-AUT-2026-001`).

> **Physical Terminal Numbering Note**: The technical report (`docs/relatorio/`) specifies logical I/O channels (`DI0..DI3` / `%IX0.0..%IX0.3`, `DO0..DO3` / `%QX0.0..%QX0.3`, `+24 Vcc`, `0 Vcc`, `ETH1`, `ETH2`). During physical panel assembly, installers **must verify the exact physical connector/housing pin numbers on the WEG PLC500 silkscreen and official WEG PLC500 User Manual**.

---

## Directory Layout

| Path | Purpose |
|---|---|
| `bom.csv` | 21-item UTF-8 Bill of Materials (`WEG PLC500`, `24 Vcc / 2.5 A` PSU, `6 A` 2P MCB, `2 A` fuse, TS35 DIN rail, terminal blocks, `B1..B4` pushbuttons, `L1..L4` LEDs + `R1..R4` `2.2 kΩ 0.5 W` resistors, `SW_ENG`/`SW_SUP` + firewall, Cat6 cables, wires, ferrules) |
| `wiring_table.md` | Complete terminal-to-terminal pinout, wire gauges/colors, Ethernet firewall ACL, and pre-energizing commissioning checklist |
| `latex/schematic_electrical.tex` | Standalone CircuiTikZ/TikZ schematic (`24 Vcc` power & protection, `DI0..DI3` PNP inputs with fail-safe `B4` NF, `DO0..DO3` PNP outputs with `2.2 kΩ 0.5 W` resistors & LEDs) |
| `latex/schematic_network_bench.tex` | Standalone TikZ diagram (`ETH1`/`ETH2` segmented OT/IT topology + TCP/502 firewall + TS35 DIN-rail & operator bench layout) |
| `diagrams/` | Rendered outputs: `electrical_wiring.{pdf,svg,png}` and `network_and_bench_layout.{pdf,svg,png}` (300 DPI PNGs) |
| `scripts/generate_schematics.py` | Automated isolated build script (`pdflatex` -> PDF + strict `.log` audit, `pypdfium2` -> 300 DPI PNG, `latex` + `dvisvgm` -> vector SVG) |
| `kicad/plc500_bench.kicad_pro` | KiCad 8 project metadata & netclasses (`Default`, `Power_24VDC`) |
| `kicad/plc500_bench.kicad_sch` | KiCad 8 S-expression schematic (`version 20231120`, `paper "A3"`, embedded `lib_symbols`, 67 pins: 59 wired + 8 `no_connect` spare I/Os) |
| `scripts/validate_kicad.py` | Pure-Python 5-layer KiCad 8 validator (L1 balanced S-expr AST, L2 header & unique UUIDs, L3 `.kicad_pro` JSON, L4 `lib_symbols` & unique refs, L5 `1.27 mm` grid & zero-dangling pin-to-wire graph) |

---

## Electrical & Network Design Summary

- **Power & Protection**: `127/220 Vca` mains -> 2P `6 A` Curve-C MCB (`Q1`) -> regulated `24 Vcc / 2.5 A (60 W)` DIN PSU (`PS1`) -> `2 A` DC fuse (`F1`) -> `+24 Vcc` (`TB_24V`) and `0 Vcc` (`TB_0V`) distribution buses (`1.5 mm²`), with protective earth (`PE`, Green/Yellow) bonded to `PS1:PE`, `RAIL1`, and `PLC500`.
- **Digital Inputs (`24 Vcc PNP`)**:
  - `DI0` (`%IX0.0`): `B1_SensPresenca` — NA pushbutton, `50 ms` `TON` debounce.
  - `DI1` (`%IX0.1`): `B2_SensNivelAlto` — NA pushbutton, `50 ms` `TON` debounce.
  - `DI2` (`%IX0.2`): `B3_SensSobretemp` — NA pushbutton, `50 ms` `TON` debounce.
  - `DI3` (`%IX0.3`): `B4_Emergencia` — **NF (NC) fail-safe** latching mushroom pushbutton, **0 ms debounce** (`+24 Vcc` = OK, `0 Vcc` / broken wire = immediate latch in `<= 10 ms`).
- **Digital Outputs (`24 Vcc PNP`)**:
  - `DO0` (`%QX0.0`): `L1_AtuMotor` (Green LED) + `R1` (`2.2 kΩ, 0.5 W`) -> `0 Vcc`.
  - `DO1` (`%QX0.1`): `L2_AtuValvula` (Yellow LED) + `R2` (`2.2 kΩ, 0.5 W`) -> `0 Vcc`.
  - `DO2` (`%QX0.2`): `L3_SinalAlarme` (Red LED) + `R3` (`2.2 kΩ, 0.5 W`) -> `0 Vcc`.
  - `DO3` (`%QX0.3`): `L4_StatusOper` (White LED) + `R4` (`2.2 kΩ, 0.5 W`) -> `0 Vcc` (solid ON when remote healthy; no blinking).
- **Network Segmentation**:
  - `ETH1` (Engineering): `192.168.20.10/24` <-> CODESYS Workstation (`192.168.20.50/24`) via `SW_ENG`.
  - `ETH2` (Supervision): `192.168.10.10/24:502` (`Unit ID 1`) <-> IT Host (`192.168.10.20/24`) via `SW_SUP` with firewall restricting TCP/502 strictly to `192.168.10.20`.

---

## Contract Reconciliations (`spec_ot.md`)

To maintain 100% traceability across the ABNT report (`docs/relatorio/`), the Structured Text PLC layer (`plc/`), the Python/Node-RED IT layer (`python/`, `node-red/`), and the hardware artifacts here:

1. **DO Limiting Resistors (`2.2 kΩ / 0.5 W`)**: `03_arquitetura.tex` and `apendice_a_checklist.tex` specify `1.8 kΩ` to `2.2 kΩ` at `0.5 W` (`~10 mA`) or integrated `24 Vcc` industrial LEDs. This hardware design standardizes on **`2.2 kΩ ±5%, 0.5 W`** (`I_DO ≈ 9.9 mA`, `P_R ≈ 0.22 W`) and notes integrated `24 Vcc` pilot lights as a drop-in equivalent.
2. **`DO3` (`L4_StatusOper`) During Overtemperature**: In `04_implementacao_clp.tex` literal code line 141 (`%QX0.3 := NOT xFlag_WatchdogAtivo;`) vs prose line 165 (`NOT xFlag_WatchdogAtivo AND xCmd_HabRemoto`), both behave identically whenever remote mode is enabled (`xCmd_HabRemoto = TRUE`); when `HabRemoto = FALSE` during overtemp, consult the ST/simulator contract for exact `DO3` state.
3. **`HR[0]` Heartbeat Alias**: Named `uiHeartbeat_NR` in the report and `uiHeartbeat_Sup` (alias `uiHeartbeat_N8N`) in the monorepo contract — all refer to Modbus Holding Register **`HR[0]`**.
4. **`IR[3]` (`uiCnt_Emergencia`)**: Mapped at Modbus Input Register `IR[3]` in the PLC and `init.sql` (omitted only from the abbreviated SQL snippet in Chapter 5 of the report).
5. **`Coil[3]` (`xCmd_ResetFalha`) Reset Guard**: Requires **both** `DI3 = 1` (`xDI_EmergenciaOK = TRUE`) **and** `DI2 = 0` (`xDI_Sobretemp = FALSE`), using rising-edge / pulse semantics (`R_TRIG`) so a stuck-high reset coil cannot bypass a future E-stop restoration.

---

## Reproducing Diagrams & Running KiCad Validation

```bash
# 1. Regenerate electrical & network/bench diagrams (PDF, 300 DPI PNG, vector SVG)
/home/augusto_dantas/sla/.venv/bin/python /home/augusto_dantas/sla/hardware/scripts/generate_schematics.py

# 2. Validate KiCad 8 project & schematic (Layers L1-L5)
/home/augusto_dantas/sla/.venv/bin/python /home/augusto_dantas/sla/hardware/scripts/validate_kicad.py
```
