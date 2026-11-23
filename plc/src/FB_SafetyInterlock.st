FUNCTION_BLOCK FB_SafetyInterlock
VAR_INPUT
  xDI_EmergenciaOK    : BOOL; (* DI[3]: Contato NF; FALSE = emergencia ativa / fio partido *)
  xDI_Sobretemp       : BOOL; (* DI[2]: Termostato filtrado *)
  xDI_NivelAlto       : BOOL; (* DI[1]: Nivel alto filtrado *)
  xFlag_WatchdogAtivo : BOOL; (* DI[5]: Watchdog expirado *)
  xCmd_Motor          : BOOL; (* Coil[0] *)
  xCmd_Valvula        : BOOL; (* Coil[1] *)
  xCmd_Alarme         : BOOL; (* Coil[2] *)
  xCmd_ResetFalha     : BOOL; (* Coil[3] *)
  xCmd_HabRemoto      : BOOL; (* Coil[4] *)
  uiSeveridade_IA     : UINT; (* HR[2] *)
END_VAR
VAR_OUTPUT
  xFlag_EmergTravada  : BOOL := FALSE; (* DI[4] *)
  uiEstado_CLP        : UINT := 0;     (* IR[4] *)
  uiCod_Intertrav     : UINT := 0;     (* IR[7] *)
  xDO0_Motor          : BOOL := FALSE; (* %QX0.0 *)
  xDO1_Valvula        : BOOL := FALSE; (* %QX0.1 *)
  xDO2_Alarme         : BOOL := FALSE; (* %QX0.2 *)
  xDO3_StatusOper     : BOOL := FALSE; (* %QX0.3 *)
  wMascara_DO_Real    : WORD := 0;     (* IR[5] *)
END_VAR
VAR
  rtResetFalha        : STANDARD.R_TRIG;
END_VAR

(* Deteccao de borda de subida no comando de rearme executada em todo ciclo sem mutar Coil[3] *)
rtResetFalha(CLK := xCmd_ResetFalha);

(* Travamento de emergencia fail-safe e rearme seguro *)
IF NOT xDI_EmergenciaOK THEN
  xFlag_EmergTravada := TRUE;
ELSIF xCmd_ResetFalha AND rtResetFalha.Q AND xDI_EmergenciaOK AND NOT xDI_Sobretemp THEN
  xFlag_EmergTravada := FALSE;
END_IF;

(* Maquina de estados e intertravamento local com precedencia estrita *)
IF xFlag_EmergTravada THEN
  uiEstado_CLP    := 4;
  uiCod_Intertrav := 1;
  xDO0_Motor      := FALSE;
  xDO1_Valvula    := FALSE;
  xDO2_Alarme     := TRUE;
  xDO3_StatusOper := FALSE;
ELSIF xDI_Sobretemp THEN
  uiEstado_CLP    := 2;
  uiCod_Intertrav := 2;
  xDO0_Motor      := FALSE;
  xDO1_Valvula    := TRUE;
  xDO2_Alarme     := TRUE;
  (* Reconciliacao 04_implementacao_clp.tex:141 vs :165/PROJECT.md:110: exige modo remoto ativo alem de watchdog saudavel *)
  xDO3_StatusOper := (NOT xFlag_WatchdogAtivo) AND xCmd_HabRemoto;
ELSIF xFlag_WatchdogAtivo THEN
  uiEstado_CLP    := 3;
  uiCod_Intertrav := 3;
  xDO0_Motor      := FALSE;
  xDO1_Valvula    := xDI_NivelAlto;
  xDO2_Alarme     := TRUE;
  xDO3_StatusOper := FALSE;
ELSIF xCmd_HabRemoto THEN
  uiCod_Intertrav := 0;
  uiEstado_CLP    := SEL((uiSeveridade_IA > 0) OR xDI_NivelAlto, UINT#1, UINT#2);
  xDO0_Motor      := xCmd_Motor;
  xDO1_Valvula    := xCmd_Valvula OR xDI_NivelAlto;
  xDO2_Alarme     := xCmd_Alarme OR (uiSeveridade_IA >= 2);
  xDO3_StatusOper := TRUE;
ELSE
  uiEstado_CLP    := 0;
  uiCod_Intertrav := 0;
  xDO0_Motor      := FALSE;
  xDO1_Valvula    := FALSE;
  xDO2_Alarme     := FALSE;
  xDO3_StatusOper := FALSE;
END_IF;

(* Espelhamento das saidas fisicas reais em bitmask (IR[5]) *)
wMascara_DO_Real := BOOL_TO_WORD(xDO0_Motor)
                 OR SHL(BOOL_TO_WORD(xDO1_Valvula), 1)
                 OR SHL(BOOL_TO_WORD(xDO2_Alarme), 2)
                 OR SHL(BOOL_TO_WORD(xDO3_StatusOper), 3);
END_FUNCTION_BLOCK
