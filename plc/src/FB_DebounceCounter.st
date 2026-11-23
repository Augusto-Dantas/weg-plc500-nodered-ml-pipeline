FUNCTION_BLOCK FB_DebounceCounter
VAR_INPUT
  xRawInput       : BOOL;           (* Sinal fisico bruto (%IX0.x) *)
  xEnableDebounce : BOOL := TRUE;   (* TRUE = filtro TON (DI0..DI2); FALSE = imediato sem retardo (DI3 NF) *)
  tDebounceTime   : TIME := T#50MS; (* Tempo de estabilizacao de contato *)
  xCountOnFalling : BOOL := FALSE;  (* FALSE = borda de subida (NA); TRUE = borda de descida / NOT (NF DI3) *)
END_VAR
VAR_OUTPUT
  xFiltered       : BOOL := FALSE;  (* Estado filtrado (ou direto para DI3) *)
END_VAR
VAR_IN_OUT
  uiCounter       : UINT;           (* Contador modular 16 bits (wrap natural 65535 -> 0) *)
END_VAR
VAR
  tonDebounce     : STANDARD.TON;
  rtEdge          : STANDARD.R_TRIG;
  xEdgeSignal     : BOOL := FALSE;
END_VAR

IF xEnableDebounce THEN
  tonDebounce(IN := xRawInput, PT := tDebounceTime);
  xFiltered := tonDebounce.Q;
ELSE
  (* Entrada fail-safe DI3 atua sem retardo no primeiro ciclo de varredura *)
  xFiltered := xRawInput;
END_IF;

IF xCountOnFalling THEN
  xEdgeSignal := NOT xFiltered;
ELSE
  xEdgeSignal := xFiltered;
END_IF;

rtEdge(CLK := xEdgeSignal);
IF rtEdge.Q THEN
  uiCounter := uiCounter + 1;
END_IF;
END_FUNCTION_BLOCK
