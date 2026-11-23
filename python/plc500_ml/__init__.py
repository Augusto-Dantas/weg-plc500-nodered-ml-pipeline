"""WEG PLC500 ML inference, feature engineering, safety rules, and Modbus simulator package."""

from plc500_ml.config import (
    INTERTRAV_EMERGENCIA,
    INTERTRAV_NENHUM,
    INTERTRAV_SOBRETEMP,
    INTERTRAV_WATCHDOG,
    MAP_VERSION,
    UNIT_ID,
    InterlockCode,
    PlcState,
    SeverityLevel,
)
from plc500_ml.schemas import (
    DIAGNOSTICO_ATUACAO_SCHEMA,
    ComandosAtuadores,
    DiagnosticoAtuacaoPLC500,
)

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "ComandosAtuadores",
    "DIAGNOSTICO_ATUACAO_SCHEMA",
    "DiagnosticoAtuacaoPLC500",
    "INTERTRAV_EMERGENCIA",
    "INTERTRAV_NENHUM",
    "INTERTRAV_SOBRETEMP",
    "INTERTRAV_WATCHDOG",
    "InterlockCode",
    "MAP_VERSION",
    "PlcState",
    "SeverityLevel",
    "UNIT_ID",
]
