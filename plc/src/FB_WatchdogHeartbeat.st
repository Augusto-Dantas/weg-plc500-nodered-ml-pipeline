FUNCTION_BLOCK FB_WatchdogHeartbeat
VAR_INPUT
  uiHeartbeat     : UINT;          (* HR[0]: uiHeartbeat_Sup (aliases: uiHeartbeat_NR, uiHeartbeat_N8N) *)
  uiTimeoutMs     : UINT;          (* HR[1]: uiTimeout_WD_ms [2000..30000] *)
END_VAR
VAR_OUTPUT
  uiEcho_Heartbeat    : UINT := 0;     (* IR[6]: uiEcho_Heartbeat *)
  xFlag_WatchdogAtivo : BOOL := FALSE; (* DI[5]: xFlag_WatchdogAtivo *)
  tActiveLimit        : TIME := T#5S;  (* Limiar efetivo em TIME *)
END_VAR
VAR
  tonWatchdog     : STANDARD.TON;
  uiLastHeartbeat : UINT := 0;
END_VAR

IF (uiTimeoutMs >= 2000) AND (uiTimeoutMs <= 30000) THEN
  tActiveLimit := UINT_TO_TIME(uiTimeoutMs);
ELSE
  tActiveLimit := T#5S;
END_IF;

(* Avaliar TON ANTES de atualizar uiLastHeartbeat para reiniciar por 1 ciclo na mudanca *)
tonWatchdog(
  IN := (uiHeartbeat = uiLastHeartbeat),
  PT := tActiveLimit
);

IF uiHeartbeat <> uiLastHeartbeat THEN
  uiLastHeartbeat := uiHeartbeat;
  uiEcho_Heartbeat := uiHeartbeat;
END_IF;

xFlag_WatchdogAtivo := tonWatchdog.Q;
END_FUNCTION_BLOCK
