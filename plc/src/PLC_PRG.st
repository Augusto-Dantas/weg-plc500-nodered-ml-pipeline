PROGRAM PLC_PRG
VAR
  fbDebPresenca : FB_DebounceCounter;
  fbDebNivel    : FB_DebounceCounter;
  fbDebTemp     : FB_DebounceCounter;
  rtEmergencia  : STANDARD.R_TRIG;
  fbWatchdog    : FB_WatchdogHeartbeat;
  fbInterlock   : FB_SafetyInterlock;
END_VAR

(* 1 & 2. Debounce (50 ms em DI0..DI2, imediato NF em DI3) e contadores modulares 16 bits *)
GVL_Modbus.xRaw_DI0_Presenca := %IX0.0;
fbDebPresenca(
  xRawInput       := %IX0.0,
  xEnableDebounce := TRUE,
  tDebounceTime   := T#50MS,
  xCountOnFalling := FALSE,
  uiCounter       := GVL_Modbus.uiCnt_Presenca
);
GVL_Modbus.xDI_Presenca := fbDebPresenca.xFiltered;

GVL_Modbus.xRaw_DI1_NivelAlto := %IX0.1;
fbDebNivel(
  xRawInput       := %IX0.1,
  xEnableDebounce := TRUE,
  tDebounceTime   := T#50MS,
  xCountOnFalling := FALSE,
  uiCounter       := GVL_Modbus.uiCnt_NivelAlto
);
GVL_Modbus.xDI_NivelAlto := fbDebNivel.xFiltered;

GVL_Modbus.xRaw_DI2_Sobretemp := %IX0.2;
fbDebTemp(
  xRawInput       := %IX0.2,
  xEnableDebounce := TRUE,
  tDebounceTime   := T#50MS,
  xCountOnFalling := FALSE,
  uiCounter       := GVL_Modbus.uiCnt_Sobretemp
);
GVL_Modbus.xDI_Sobretemp := fbDebTemp.xFiltered;

GVL_Modbus.xRaw_DI3_Emergencia := %IX0.3;
GVL_Modbus.xDI_EmergenciaOK := %IX0.3;
rtEmergencia(CLK := NOT GVL_Modbus.xDI_EmergenciaOK);
IF rtEmergencia.Q THEN
  GVL_Modbus.uiCnt_Emergencia := GVL_Modbus.uiCnt_Emergencia + 1;
END_IF;

(* 3. Supervisao de enlace Modbus TCP/IP (Watchdog via HR[0]/HR[1]) *)
fbWatchdog(
  uiHeartbeat         := GVL_Modbus.uiHeartbeat_Sup,
  uiTimeoutMs         := GVL_Modbus.uiTimeout_WD_ms,
  uiEcho_Heartbeat    => GVL_Modbus.uiEcho_Heartbeat,
  xFlag_WatchdogAtivo => GVL_Modbus.xFlag_WatchdogAtivo
);

(* 4, 5 & 6a. Travamento fail-safe, FSM com precedencia estrita e wMascara_DO_Real *)
fbInterlock(
  xDI_EmergenciaOK    := GVL_Modbus.xDI_EmergenciaOK,
  xDI_Sobretemp       := GVL_Modbus.xDI_Sobretemp,
  xDI_NivelAlto       := GVL_Modbus.xDI_NivelAlto,
  xFlag_WatchdogAtivo := GVL_Modbus.xFlag_WatchdogAtivo,
  xCmd_Motor          := GVL_Modbus.xCmd_Motor,
  xCmd_Valvula        := GVL_Modbus.xCmd_Valvula,
  xCmd_Alarme         := GVL_Modbus.xCmd_Alarme,
  xCmd_ResetFalha     := GVL_Modbus.xCmd_ResetFalha,
  xCmd_HabRemoto      := GVL_Modbus.xCmd_HabRemoto,
  uiSeveridade_IA     := GVL_Modbus.uiSeveridade_IA,
  xFlag_EmergTravada  => GVL_Modbus.xFlag_EmergTravada,
  uiEstado_CLP        => GVL_Modbus.uiEstado_CLP,
  uiCod_Intertrav     => GVL_Modbus.uiCod_Intertrav,
  xDO0_Motor          => GVL_Modbus.xDO0_Motor,
  xDO1_Valvula        => GVL_Modbus.xDO1_Valvula,
  xDO2_Alarme         => GVL_Modbus.xDO2_Alarme,
  xDO3_StatusOper     => GVL_Modbus.xDO3_StatusOper,
  wMascara_DO_Real    => GVL_Modbus.wMascara_DO_Real
);

(* Escrita direta nas saidas fisicas 24 Vcc PNP (%QX0.0..%QX0.3) *)
%QX0.0 := GVL_Modbus.xDO0_Motor;
%QX0.1 := GVL_Modbus.xDO1_Valvula;
%QX0.2 := GVL_Modbus.xDO2_Alarme;
%QX0.3 := GVL_Modbus.xDO3_StatusOper;

(* 6b. Reforco da versao do mapa Modbus a cada ciclo de 10 ms *)
GVL_Modbus.uiVersao_Mapa := 100;
END_PROGRAM
