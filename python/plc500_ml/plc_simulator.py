"""WEG PLC500 Modbus TCP Simulator (10 ms scan cycle) & Fault-Injection Control API.

Faithfully emulates the Structured Text control logic in ``plc/src/``:
- ``GVL_Modbus.st``: 0-based Modbus TCP register map (Unit ID 1, port 502 / 5020 / 8502)
- ``FB_DebounceCounter.st``: 50 ms TON debounce on DI0..DI2, 0 ms fail-safe NF on DI3,
  and 16-bit modular counters ``0..65535`` with wrap-around
- ``FB_WatchdogHeartbeat.st``: Supervisory heartbeat monitor on HR[0]/HR[1] ([2000..30000] ms,
  fallback 5000 ms) evaluated before updating ``uiLastHeartbeat``
- ``FB_SafetyInterlock.st``: Fail-safe emergency latch with guarded ``R_TRIG`` reset
  (requires ``DI3=1`` and ``DI2=0``) and 5-level strict priority tree
- ``PLC_PRG.st``: Cyclic 10 ms scan loop reinforcing ``HR[3] = 100`` every scan
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import time
from contextlib import asynccontextmanager, suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from plc500_ml.config import (
    DEBOUNCE_S,
    ESTADO_ALERTA,
    ESTADO_EMERGENCIA_TRAV,
    ESTADO_INIT,
    ESTADO_MODO_DEGRADADO,
    ESTADO_NORMAL_AUTO,
    INTERTRAV_EMERGENCIA,
    INTERTRAV_NENHUM,
    INTERTRAV_SOBRETEMP,
    INTERTRAV_WATCHDOG,
    MAP_VERSION,
    SCAN_PERIOD_S,
    UINT16_MOD,
    UNIT_ID,
    WD_DEFAULT_MS,
    WD_MAX_MS,
    WD_MIN_MS,
)

logger = logging.getLogger(__name__)


def _decode_do_mask(mask: int) -> dict[str, bool]:
    val = int(mask) & 0x0F
    return {
        "DO0": bool(val & 0b0001),
        "DO1": bool(val & 0b0010),
        "DO2": bool(val & 0b0100),
        "DO3": bool(val & 0b1000),
    }


def _resolve_di_channel(name: str) -> int:
    key = name.strip().lower()
    mapping = {
        "di0": 0,
        "0": 0,
        "xdi_presenca": 0,
        "di_presenca": 0,
        "presenca": 0,
        "b1": 0,
        "b1_senspresenca": 0,
        "di1": 1,
        "1": 1,
        "xdi_nivelalto": 1,
        "di_nivel_alto": 1,
        "nivel_alto": 1,
        "b2": 1,
        "b2_sensnivelalto": 1,
        "di2": 2,
        "2": 2,
        "xdi_sobretemp": 2,
        "di_sobretemp": 2,
        "sobretemp": 2,
        "b3": 2,
        "b3_senssobretemp": 2,
        "di3": 3,
        "3": 3,
        "xdi_emergenciaok": 3,
        "di_emergencia_ok": 3,
        "emergencia_ok": 3,
        "b4": 3,
        "b4_emergencia": 3,
    }
    if key not in mapping:
        raise ValueError(f"Unknown DI channel: {name!r}")
    return mapping[key]


class PLC500StateCore:
    """Deterministic in-memory execution model of PLC_PRG and its 3 Function Blocks."""

    def __init__(self, *, debounce_s: float = DEBOUNCE_S) -> None:
        self.debounce_s = float(debounce_s)
        self.raw_di0: bool = False
        self.raw_di1: bool = False
        self.raw_di2: bool = False
        self.raw_di3: bool = True

        self.high_since: dict[int, float | None] = {0: None, 1: None, 2: None}
        self.pulse_cap_s: dict[int, float | None] = {0: None, 1: None, 2: None}
        self.prev_edge_signal: dict[int, bool] = {0: False, 1: False, 2: False, 3: False}

        self.xDI_Presenca: bool = False
        self.xDI_NivelAlto: bool = False
        self.xDI_Sobretemp: bool = False
        self.xDI_EmergenciaOK: bool = True
        self.xFlag_EmergTravada: bool = False
        self.xFlag_WatchdogAtivo: bool = False

        self.xCmd_Motor: bool = False
        self.xCmd_Valvula: bool = False
        self.xCmd_Alarme: bool = False
        self.xCmd_ResetFalha: bool = False
        self.xCmd_HabRemoto: bool = False
        self.prev_cmd_reset_falha: bool = False

        self.uiCnt_Presenca: int = 0
        self.uiCnt_NivelAlto: int = 0
        self.uiCnt_Sobretemp: int = 0
        self.uiCnt_Emergencia: int = 0
        self.uiEstado_CLP: int = ESTADO_INIT
        self.wMascara_DO_Real: int = 0
        self.uiEcho_Heartbeat: int = 0
        self.uiCod_Intertrav: int = INTERTRAV_NENHUM

        self.uiHeartbeat_Sup: int = 0
        self.uiTimeout_WD_ms: int = WD_DEFAULT_MS
        self.uiSeveridade_IA: int = 0
        self.uiVersao_Mapa: int = MAP_VERSION

        self.uiLastHeartbeat: int = 0
        self.wd_stagnant_since: float = time.monotonic()

        self.chattering_active: bool = False
        self._last_chatter_toggle: float = 0.0

    def reset(self, *, now: float | None = None, reset_counters: bool = True) -> None:
        """Reset PLC state to cold-boot defaults."""
        ts = time.monotonic() if now is None else now
        self.raw_di0 = False
        self.raw_di1 = False
        self.raw_di2 = False
        self.raw_di3 = True

        self.high_since = {0: None, 1: None, 2: None}
        self.pulse_cap_s = {0: None, 1: None, 2: None}
        self.prev_edge_signal = {0: False, 1: False, 2: False, 3: False}

        self.xDI_Presenca = False
        self.xDI_NivelAlto = False
        self.xDI_Sobretemp = False
        self.xDI_EmergenciaOK = True
        self.xFlag_EmergTravada = False
        self.xFlag_WatchdogAtivo = False

        self.xCmd_Motor = False
        self.xCmd_Valvula = False
        self.xCmd_Alarme = False
        self.xCmd_ResetFalha = False
        self.xCmd_HabRemoto = False
        self.prev_cmd_reset_falha = False

        if reset_counters:
            self.uiCnt_Presenca = 0
            self.uiCnt_NivelAlto = 0
            self.uiCnt_Sobretemp = 0
            self.uiCnt_Emergencia = 0

        self.uiEstado_CLP = ESTADO_INIT
        self.wMascara_DO_Real = 0
        self.uiEcho_Heartbeat = 0
        self.uiCod_Intertrav = INTERTRAV_NENHUM

        self.uiHeartbeat_Sup = 0
        self.uiTimeout_WD_ms = WD_DEFAULT_MS
        self.uiSeveridade_IA = 0
        self.uiVersao_Mapa = MAP_VERSION

        self.uiLastHeartbeat = 0
        self.wd_stagnant_since = ts
        self.chattering_active = False
        self._last_chatter_toggle = ts

    def set_raw_channel(self, channel_idx: int, value: bool, *, now: float | None = None) -> None:
        """Set raw physical input %IX0.0..%IX0.3."""
        ts = time.monotonic() if now is None else now
        val = bool(value)
        if channel_idx == 0:
            if val and not self.raw_di0:
                self.high_since[0] = ts
            elif not val:
                self.high_since[0] = None
            self.raw_di0 = val
        elif channel_idx == 1:
            if val and not self.raw_di1:
                self.high_since[1] = ts
            elif not val:
                self.high_since[1] = None
            self.raw_di1 = val
        elif channel_idx == 2:
            if val and not self.raw_di2:
                self.high_since[2] = ts
            elif not val:
                self.high_since[2] = None
            self.raw_di2 = val
        elif channel_idx == 3:
            self.raw_di3 = val
        else:
            raise ValueError(f"Invalid channel index: {channel_idx}")

    def _eval_debounce_channel(self, ch: int, raw_val: bool, now: float) -> bool:
        if not raw_val:
            self.high_since[ch] = None
            filtered = False
        else:
            if self.high_since[ch] is None:
                self.high_since[ch] = now
            elapsed = max(0.0, now - self.high_since[ch])
            cap = self.pulse_cap_s[ch]
            if cap is not None:
                elapsed = min(elapsed, cap)
            filtered = elapsed >= self.debounce_s

        if filtered and not self.prev_edge_signal[ch]:
            if ch == 0:
                self.uiCnt_Presenca = (self.uiCnt_Presenca + 1) % UINT16_MOD
            elif ch == 1:
                self.uiCnt_NivelAlto = (self.uiCnt_NivelAlto + 1) % UINT16_MOD
            elif ch == 2:
                self.uiCnt_Sobretemp = (self.uiCnt_Sobretemp + 1) % UINT16_MOD
        self.prev_edge_signal[ch] = filtered
        return filtered

    def step(self, now: float | None = None) -> None:
        """Execute one 10 ms PLC_PRG scan cycle."""
        ts = time.monotonic() if now is None else now

        # Optional background chattering generator for CT-04
        if self.chattering_active and (ts - self._last_chatter_toggle) >= 0.060:
            self.set_raw_channel(0, not self.raw_di0, now=ts)
            self._last_chatter_toggle = ts

        # 1 & 2. FB_DebounceCounter on DI0..DI2 (TON 50 ms) and DI3 (0 ms NF)
        self.xDI_Presenca = self._eval_debounce_channel(0, self.raw_di0, ts)
        self.xDI_NivelAlto = self._eval_debounce_channel(1, self.raw_di1, ts)
        self.xDI_Sobretemp = self._eval_debounce_channel(2, self.raw_di2, ts)

        self.xDI_EmergenciaOK = bool(self.raw_di3)
        edge_emerg = not self.xDI_EmergenciaOK
        if edge_emerg and not self.prev_edge_signal[3]:
            self.uiCnt_Emergencia = (self.uiCnt_Emergencia + 1) % UINT16_MOD
        self.prev_edge_signal[3] = edge_emerg

        # 3. FB_WatchdogHeartbeat (evaluate TON before updating uiLastHeartbeat)
        if WD_MIN_MS <= self.uiTimeout_WD_ms <= WD_MAX_MS:
            wd_limit_s = float(self.uiTimeout_WD_ms) / 1000.0
        else:
            wd_limit_s = float(WD_DEFAULT_MS) / 1000.0

        if self.uiHeartbeat_Sup == self.uiLastHeartbeat:
            self.xFlag_WatchdogAtivo = (ts - self.wd_stagnant_since) >= wd_limit_s
        else:
            self.xFlag_WatchdogAtivo = False
            self.wd_stagnant_since = ts
            self.uiLastHeartbeat = self.uiHeartbeat_Sup
            self.uiEcho_Heartbeat = self.uiHeartbeat_Sup

        # 4 & 5. FB_SafetyInterlock (R_TRIG on Coil[3], fail-safe latch, strict priority FSM)
        rt_reset_q = self.xCmd_ResetFalha and (not self.prev_cmd_reset_falha)
        self.prev_cmd_reset_falha = self.xCmd_ResetFalha

        if not self.xDI_EmergenciaOK:
            self.xFlag_EmergTravada = True
        elif rt_reset_q and self.xDI_EmergenciaOK and (not self.xDI_Sobretemp):
            self.xFlag_EmergTravada = False

        if self.xFlag_EmergTravada:
            self.uiEstado_CLP = ESTADO_EMERGENCIA_TRAV
            self.uiCod_Intertrav = INTERTRAV_EMERGENCIA
            do0, do1, do2, do3 = False, False, True, False
        elif self.xDI_Sobretemp:
            self.uiEstado_CLP = ESTADO_ALERTA
            self.uiCod_Intertrav = INTERTRAV_SOBRETEMP
            do0, do1, do2 = False, True, True
            do3 = (not self.xFlag_WatchdogAtivo) and self.xCmd_HabRemoto
        elif self.xFlag_WatchdogAtivo:
            self.uiEstado_CLP = ESTADO_MODO_DEGRADADO
            self.uiCod_Intertrav = INTERTRAV_WATCHDOG
            do0 = False
            do1 = self.xDI_NivelAlto
            do2 = True
            do3 = False
        elif self.xCmd_HabRemoto:
            self.uiCod_Intertrav = INTERTRAV_NENHUM
            self.uiEstado_CLP = (
                ESTADO_ALERTA
                if ((self.uiSeveridade_IA > 0) or self.xDI_NivelAlto)
                else ESTADO_NORMAL_AUTO
            )
            do0 = self.xCmd_Motor
            do1 = self.xCmd_Valvula or self.xDI_NivelAlto
            do2 = self.xCmd_Alarme or (self.uiSeveridade_IA >= 2)
            do3 = True
        else:
            self.uiEstado_CLP = ESTADO_INIT
            self.uiCod_Intertrav = INTERTRAV_NENHUM
            do0, do1, do2, do3 = False, False, False, False

        self.wMascara_DO_Real = (
            int(bool(do0)) | (int(bool(do1)) << 1) | (int(bool(do2)) << 2) | (int(bool(do3)) << 3)
        )

        # 6b. Reinforce fixed Modbus map version every scan cycle
        self.uiVersao_Mapa = MAP_VERSION

    def get_dis(self) -> list[bool]:
        return [
            bool(self.xDI_Presenca),
            bool(self.xDI_NivelAlto),
            bool(self.xDI_Sobretemp),
            bool(self.xDI_EmergenciaOK),
            bool(self.xFlag_EmergTravada),
            bool(self.xFlag_WatchdogAtivo),
        ]

    def get_coils(self) -> list[bool]:
        return [
            bool(self.xCmd_Motor),
            bool(self.xCmd_Valvula),
            bool(self.xCmd_Alarme),
            bool(self.xCmd_ResetFalha),
            bool(self.xCmd_HabRemoto),
        ]

    def get_irs(self) -> list[int]:
        return [
            int(self.uiCnt_Presenca) & 0xFFFF,
            int(self.uiCnt_NivelAlto) & 0xFFFF,
            int(self.uiCnt_Sobretemp) & 0xFFFF,
            int(self.uiCnt_Emergencia) & 0xFFFF,
            int(self.uiEstado_CLP) & 0xFFFF,
            int(self.wMascara_DO_Real) & 0xFFFF,
            int(self.uiEcho_Heartbeat) & 0xFFFF,
            int(self.uiCod_Intertrav) & 0xFFFF,
        ]

    def get_hrs(self) -> list[int]:
        return [
            int(self.uiHeartbeat_Sup) & 0xFFFF,
            int(self.uiTimeout_WD_ms) & 0xFFFF,
            int(self.uiSeveridade_IA) & 0xFFFF,
            int(self.uiVersao_Mapa) & 0xFFFF,
        ]

    def snapshot(self) -> dict[str, Any]:
        dis = self.get_dis()
        coils = self.get_coils()
        irs = self.get_irs()
        hrs = self.get_hrs()
        return {
            "status": "ok",
            "raw_inputs": {
                "di0": self.raw_di0,
                "di1": self.raw_di1,
                "di2": self.raw_di2,
                "di3": self.raw_di3,
            },
            "xDI_Presenca": dis[0],
            "xDI_NivelAlto": dis[1],
            "xDI_Sobretemp": dis[2],
            "xDI_EmergenciaOK": dis[3],
            "xFlag_EmergTravada": dis[4],
            "xFlag_WatchdogAtivo": dis[5],
            "xCmd_Motor": coils[0],
            "xCmd_Valvula": coils[1],
            "xCmd_Alarme": coils[2],
            "xCmd_ResetFalha": coils[3],
            "xCmd_HabRemoto": coils[4],
            "uiCnt_Presenca": irs[0],
            "uiCnt_NivelAlto": irs[1],
            "uiCnt_Sobretemp": irs[2],
            "uiCnt_Emergencia": irs[3],
            "uiEstado_CLP": irs[4],
            "wMascara_DO_Real": irs[5],
            "uiEcho_Heartbeat": irs[6],
            "uiCod_Intertrav": irs[7],
            "uiHeartbeat_Sup": hrs[0],
            "uiTimeout_WD_ms": hrs[1],
            "uiSeveridade_IA": hrs[2],
            "uiVersao_Mapa": hrs[3],
            "do": _decode_do_mask(irs[5]),
            "dis": dis,
            "coils": coils,
            "irs": irs,
            "hrs": hrs,
            "chattering_active": self.chattering_active,
        }


class PLC500ModbusSimulator:
    """Async Modbus TCP Server + 10 ms PLC Scan Loop controller."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        modbus_port: int = 5020,
        unit_id: int = UNIT_ID,
        scan_period_s: float = SCAN_PERIOD_S,
    ) -> None:
        self.host = host
        self.modbus_port = int(modbus_port)
        self.unit_id = int(unit_id)
        self.scan_period_s = float(scan_period_s)
        self.core = PLC500StateCore()
        self.server: ModbusTcpServer | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._running = False
        self._lock = asyncio.Lock()

    async def _on_modbus_action(
        self,
        func_code: int,
        _start_address: int,
        _address: int,
        _count: int,
        _current_registers: list[int],
        set_values: list[int] | list[bool] | None,
    ) -> None:
        """Schedule an immediate PLC scan evaluation right after any Modbus write."""
        if set_values is not None and func_code in (5, 6, 15, 16):
            loop = asyncio.get_running_loop()
            loop.call_soon(lambda: asyncio.create_task(self.scan_step()))
        return None

    def _build_sim_device(self) -> SimDevice:
        return SimDevice(
            id=0,
            simdata=(
                [SimData(0, count=16, values=False, datatype=DataType.BITS)],
                [SimData(0, count=16, values=False, datatype=DataType.BITS)],
                [SimData(0, count=16, values=0, datatype=DataType.REGISTERS)],
                [SimData(0, count=16, values=0, datatype=DataType.REGISTERS)],
            ),
            action=self._on_modbus_action,
        )

    async def _push_all_to_datastore(self) -> None:
        if self.server is None:
            return
        await self.server.async_setValues(self.unit_id, 1, 0, self.core.get_coils())
        await self.server.async_setValues(self.unit_id, 2, 0, self.core.get_dis())
        await self.server.async_setValues(self.unit_id, 3, 0, self.core.get_hrs())
        await self.server.async_setValues(self.unit_id, 4, 0, self.core.get_irs())

    async def scan_step(self, *, now: float | None = None) -> dict[str, Any]:
        """Pull Coils/HRs from Modbus datastore, execute 1 scan step, and push DIs/IRs/HR[3]."""
        async with self._lock:
            if self.server is not None:
                coils = await self.server.async_getValues(self.unit_id, 1, 0, 5)
                hrs = await self.server.async_getValues(self.unit_id, 3, 0, 4)

                self.core.xCmd_Motor = bool(coils[0])
                self.core.xCmd_Valvula = bool(coils[1])
                self.core.xCmd_Alarme = bool(coils[2])
                self.core.xCmd_ResetFalha = bool(coils[3])
                self.core.xCmd_HabRemoto = bool(coils[4])

                self.core.uiHeartbeat_Sup = int(hrs[0]) & 0xFFFF
                self.core.uiTimeout_WD_ms = int(hrs[1]) & 0xFFFF
                self.core.uiSeveridade_IA = int(hrs[2]) & 0xFFFF

            self.core.step(now=now)

            if self.server is not None:
                await self.server.async_setValues(self.unit_id, 2, 0, self.core.get_dis())
                await self.server.async_setValues(self.unit_id, 4, 0, self.core.get_irs())
                await self.server.async_setValues(self.unit_id, 3, 3, [MAP_VERSION])

            return self.core.snapshot()

    async def _scan_loop(self) -> None:
        while self._running:
            await self.scan_step()
            await asyncio.sleep(self.scan_period_s)

    async def start(self) -> None:
        """Start the Modbus TCP server and 10 ms background scan loop."""
        if self._running:
            return
        dev = self._build_sim_device()
        self.server = ModbusTcpServer(dev, address=(self.host, self.modbus_port))
        self.core.reset()
        await self._push_all_to_datastore()
        await self.server.serve_forever(background=True)
        self._running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info(
            "PLC500 Modbus TCP simulator listening on %s:%d (unit_id=%d)",
            self.host,
            self.modbus_port,
            self.unit_id,
        )

    async def stop(self) -> None:
        """Stop the 10 ms scan loop and shut down the Modbus TCP server."""
        self._running = False
        if self._scan_task is not None:
            self._scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        if self.server is not None:
            await self.server.shutdown()
            self.server = None

    async def reset(self, *, reset_counters: bool = True) -> dict[str, Any]:
        """Reset simulator state and synchronize all Modbus tables."""
        async with self._lock:
            self.core.reset(reset_counters=reset_counters)
            await self._push_all_to_datastore()
            return self.core.snapshot()

    async def set_inputs(
        self,
        *,
        di0: bool | None = None,
        di1: bool | None = None,
        di2: bool | None = None,
        di3: bool | None = None,
    ) -> dict[str, Any]:
        """Update raw physical inputs DI0..DI3 and run an immediate scan evaluation."""
        now = time.monotonic()
        async with self._lock:
            if di0 is not None:
                self.core.chattering_active = False
                self.core.set_raw_channel(0, di0, now=now)
            if di1 is not None:
                self.core.set_raw_channel(1, di1, now=now)
            if di2 is not None:
                self.core.set_raw_channel(2, di2, now=now)
            if di3 is not None:
                self.core.set_raw_channel(3, di3, now=now)
        return await self.scan_step(now=now)

    async def pulse_di(
        self,
        channel: str,
        duration_ms: int,
        *,
        value: bool | None = None,
    ) -> dict[str, Any]:
        """Apply a timed physical pulse on DI0..DI3 with deterministic debounce behavior."""
        ch_idx = _resolve_di_channel(channel)
        active_val = bool(value) if value is not None else (ch_idx != 3)
        inactive_val = not active_val
        duration_s = max(0.001, float(duration_ms) / 1000.0)

        if ch_idx in (0, 1, 2):
            t0 = time.monotonic()
            async with self._lock:
                if ch_idx == 0:
                    self.core.chattering_active = False
                self.core.pulse_cap_s[ch_idx] = duration_s
                self.core.set_raw_channel(ch_idx, active_val, now=t0)
                self.core.high_since[ch_idx] = t0
            await self.scan_step(now=t0)
            await asyncio.sleep(min(duration_s, 0.30))
            await self.scan_step(now=t0 + duration_s)
            async with self._lock:
                self.core.pulse_cap_s[ch_idx] = None
                self.core.set_raw_channel(ch_idx, inactive_val, now=time.monotonic())
            return await self.scan_step(now=time.monotonic())

        # DI3 NF fail-safe: zero debounce (0 ms) -> immediate latch on drop
        t0 = time.monotonic()
        async with self._lock:
            self.core.set_raw_channel(3, active_val, now=t0)
        await self.scan_step(now=t0)
        await asyncio.sleep(min(duration_s, 0.30))
        async with self._lock:
            self.core.set_raw_channel(3, inactive_val, now=time.monotonic())
        return await self.scan_step(now=time.monotonic())


class InputsUpdateRequest(BaseModel):
    """Payload for POST /inputs updating raw DI0..DI3 channels."""

    model_config = ConfigDict(extra="ignore")

    di0: bool | None = None
    di1: bool | None = None
    di2: bool | None = None
    di3: bool | None = None
    xdi_presenca: bool | None = None
    xdi_nivelalto: bool | None = None
    xdi_sobretemp: bool | None = None
    xdi_emergenciaok: bool | None = None
    di_presenca: bool | None = None
    di_nivel_alto: bool | None = None
    di_sobretemp: bool | None = None
    di_emergencia_ok: bool | None = None
    presenca: bool | None = None
    nivel_alto: bool | None = None
    sobretemp: bool | None = None
    emergencia_ok: bool | None = None
    b1: bool | None = None
    b2: bool | None = None
    b3: bool | None = None
    b4: bool | None = None

    def resolved_updates(self) -> dict[str, bool | None]:
        def _pick(*vals: bool | None) -> bool | None:
            return next((v for v in vals if v is not None), None)

        return {
            "di0": _pick(self.di0, self.xdi_presenca, self.di_presenca, self.presenca, self.b1),
            "di1": _pick(
                self.di1, self.xdi_nivelalto, self.di_nivel_alto, self.nivel_alto, self.b2
            ),
            "di2": _pick(self.di2, self.xdi_sobretemp, self.di_sobretemp, self.sobretemp, self.b3),
            "di3": _pick(
                self.di3,
                self.xdi_emergenciaok,
                self.di_emergencia_ok,
                self.emergencia_ok,
                self.b4,
            ),
        }


class PulseRequest(BaseModel):
    """Payload for POST /pulse injecting a timed contact bounce or pulse."""

    model_config = ConfigDict(extra="ignore")

    channel: str = "di0"
    duration_ms: int = Field(default=100, ge=1, le=60000)
    value: bool | None = None


class FaultInjectionRequest(BaseModel):
    """Payload for POST /fault supporting Node-RED Tab 5 CT-01..CT-09 scenarios."""

    model_config = ConfigDict(extra="ignore")

    test_case: str | None = None
    scenario: str | None = None
    active: bool = True
    channel: str | None = None
    pulse_ms: int | None = None
    duration_ms: int | None = None
    di0: bool | None = None
    di1: bool | None = None
    di2: bool | None = None
    di3: bool | None = None
    di_presenca: bool | None = None
    di_nivel_alto: bool | None = None
    di_sobretemp: bool | None = None
    di_emergencia_ok: bool | None = None


class ResetRequest(BaseModel):
    """Payload for POST /reset restoring nominal PLC state."""

    model_config = ConfigDict(extra="ignore")

    scenario: str | None = "nominal"
    reset_counters: bool = True


def create_control_app(simulator: PLC500ModbusSimulator) -> FastAPI:
    """Create the FastAPI fault-injection and inspection HTTP service."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not simulator._running:
            await simulator.start()
        try:
            yield
        finally:
            if simulator._running:
                await simulator.stop()

    app = FastAPI(
        title="WEG PLC500 Modbus Simulator Control API",
        version="1.0.0",
        description=(
            "Fault-injection, contact-pulse, and state inspection API for PLC500 simulator."
        ),
        lifespan=lifespan,
    )
    app.state.simulator = simulator

    @app.get("/health")
    async def get_health() -> dict[str, Any]:
        snap = simulator.core.snapshot()
        return {
            "status": "ok",
            "healthy": True,
            "service": "plc500-modbus-simulator",
            "modbus_host": simulator.host,
            "modbus_port": simulator.modbus_port,
            "unit_id": simulator.unit_id,
            "uiVersao_Mapa": snap["uiVersao_Mapa"],
            "uiEstado_CLP": snap["uiEstado_CLP"],
        }

    @app.get("/state")
    @app.get("/status")
    @app.get("/inputs")
    async def get_state() -> dict[str, Any]:
        return await simulator.scan_step()

    @app.post("/inputs")
    @app.put("/inputs")
    @app.patch("/inputs")
    async def update_inputs(payload: InputsUpdateRequest) -> dict[str, Any]:
        updates = payload.resolved_updates()
        return await simulator.set_inputs(**updates)

    @app.post("/pulse")
    async def pulse_input(payload: PulseRequest) -> dict[str, Any]:
        try:
            return await simulator.pulse_di(
                payload.channel,
                payload.duration_ms,
                value=payload.value,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/fault")
    async def inject_fault(payload: FaultInjectionRequest) -> dict[str, Any]:
        pulse_width = payload.pulse_ms or payload.duration_ms
        if pulse_width is not None and payload.channel:
            return await simulator.pulse_di(payload.channel, int(pulse_width))

        if payload.scenario == "chattering_b1":
            simulator.core.chattering_active = bool(payload.active)
            return await simulator.scan_step()
        if payload.scenario == "nominal":
            return await simulator.reset()

        di0 = payload.di0 if payload.di0 is not None else payload.di_presenca
        di1 = payload.di1 if payload.di1 is not None else payload.di_nivel_alto
        di2 = payload.di2 if payload.di2 is not None else payload.di_sobretemp
        di3 = payload.di3 if payload.di3 is not None else payload.di_emergencia_ok
        return await simulator.set_inputs(di0=di0, di1=di1, di2=di2, di3=di3)

    @app.post("/reset")
    async def reset_simulator(payload: ResetRequest | None = None) -> dict[str, Any]:
        reset_counters = True if payload is None else payload.reset_counters
        return await simulator.reset(reset_counters=reset_counters)

    return app


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WEG PLC500 Modbus TCP Simulator (10 ms scan cycle) + Fault-Injection API."
    )
    default_host = os.getenv("PLC_SIM_HOST", "0.0.0.0")
    default_modbus_port = int(os.getenv("PLC_SIM_PORT", os.getenv("PLC_PORT", "502")))
    default_api_host = os.getenv("PLC_SIM_API_HOST", default_host)
    default_api_port = int(
        os.getenv("PLC_SIM_API_PORT") or os.getenv("PLC_SIM_CONTROL_PORT") or "8502"
    )
    default_unit_id = int(os.getenv("PLC_UNIT_ID", os.getenv("UNIT_ID", str(UNIT_ID))))

    parser.add_argument(
        "--host",
        "--modbus-host",
        dest="host",
        default=default_host,
        help="Modbus TCP bind host (default: PLC_SIM_HOST or 0.0.0.0).",
    )
    parser.add_argument(
        "--modbus-port",
        "--port",
        dest="modbus_port",
        type=int,
        default=default_modbus_port,
        help="Modbus TCP bind port (default: PLC_SIM_PORT or 502).",
    )
    parser.add_argument(
        "--api-host",
        dest="api_host",
        default=default_api_host,
        help="Fault-injection HTTP API bind host (default: PLC_SIM_API_HOST or --host).",
    )
    parser.add_argument(
        "--api-port",
        "--control-port",
        dest="api_port",
        type=int,
        default=default_api_port,
        help="Fault-injection HTTP API bind port (default: PLC_SIM_API_PORT or 8502).",
    )
    parser.add_argument(
        "--unit-id",
        dest="unit_id",
        type=int,
        default=default_unit_id,
        help="Modbus Unit ID (default: 1).",
    )
    parser.add_argument(
        "--scan-ms",
        dest="scan_ms",
        type=float,
        default=SCAN_PERIOD_S * 1000.0,
        help="PLC scan cycle period in milliseconds (default: 10.0).",
    )
    return parser


async def serve_simulator(
    *,
    host: str,
    modbus_port: int,
    api_host: str,
    api_port: int,
    unit_id: int = UNIT_ID,
    scan_period_s: float = SCAN_PERIOD_S,
) -> None:
    """Run Modbus TCP server, 10 ms PLC scan loop, and FastAPI control API concurrently."""
    simulator = PLC500ModbusSimulator(
        host=host,
        modbus_port=modbus_port,
        unit_id=unit_id,
        scan_period_s=scan_period_s,
    )
    app = create_control_app(simulator)
    config = uvicorn.Config(
        app,
        host=api_host,
        port=api_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, server.handle_exit, sig, None)

    await server.serve()


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = build_arg_parser().parse_args()
    asyncio.run(
        serve_simulator(
            host=args.host,
            modbus_port=args.modbus_port,
            api_host=args.api_host,
            api_port=args.api_port,
            unit_id=args.unit_id,
            scan_period_s=max(0.001, args.scan_ms / 1000.0),
        )
    )


if __name__ == "__main__":
    main()
