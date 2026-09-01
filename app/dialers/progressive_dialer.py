from dataclasses import replace

from app.dialers.base import DialerBase
from app.pacing.pacing_engine import PacingEngineConfig

PROGRESSIVE_ANSWER_RATE = 1.0
PROGRESSIVE_SAFETY_MARGIN = 1.0
PROGRESSIVE_SOON_FREE_WEIGHT = 0.0


class ProgressiveDialer(DialerBase):
    def engine_config(self) -> PacingEngineConfig:
        config = super().engine_config()
        return replace(
            config,
            forced_answer_rate=PROGRESSIVE_ANSWER_RATE,
            safety_margin=PROGRESSIVE_SAFETY_MARGIN,
            soon_free_weight=PROGRESSIVE_SOON_FREE_WEIGHT,
        )
