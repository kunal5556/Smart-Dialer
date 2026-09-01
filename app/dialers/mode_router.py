from app.dialers.base import DialerBase
from app.dialers.predictive_dialer import PredictiveDialer
from app.dialers.progressive_dialer import ProgressiveDialer
from app.models.campaign import Campaign
from app.models.enums import DialingMode


class ModeRouter:
    def __init__(
        self,
        progressive_dialer: ProgressiveDialer,
        predictive_dialer: PredictiveDialer,
    ) -> None:
        self._dialers: dict[DialingMode, DialerBase] = {
            DialingMode.PROGRESSIVE: progressive_dialer,
            DialingMode.PREDICTIVE: predictive_dialer,
        }

    def select(self, campaign: Campaign) -> DialerBase:
        return self._dialers[campaign.dialing_mode]
