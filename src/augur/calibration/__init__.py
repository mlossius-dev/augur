"""
Augur calibration layer — source weight and lens parameter tuning via
retroactive replay-mode signal survival scoring.
"""

from augur.calibration.models import (
    CalibrationReport,
    CalibrationRun,
    CalibrationStatus,
    LensScore,
    SignalOutcome,
    SourceScore,
)
from augur.calibration.orchestrator import CalibrationOrchestrator
from augur.calibration.weight_store import (
    get_effective_weight,
    load_all_overrides,
    persist_weight_overrides,
)

__all__ = [
    "CalibrationOrchestrator",
    "CalibrationRun",
    "CalibrationStatus",
    "CalibrationReport",
    "SignalOutcome",
    "SourceScore",
    "LensScore",
    "persist_weight_overrides",
    "load_all_overrides",
    "get_effective_weight",
]
