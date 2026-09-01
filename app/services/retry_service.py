from app.config import Settings
from app.models.base import utc_now
from app.models.borrower import Borrower
from app.repositories.borrower_repo import BorrowerReleaseOutcome
from app.services.provider_health import ProviderHealthManager


class RetryService:
    def __init__(self, health_manager: ProviderHealthManager, settings: Settings) -> None:
        self._health = health_manager
        self._settings = settings

    def should_retry(self, borrower: Borrower, provider_name: str) -> bool:
        if borrower.attempt_count >= self._settings.MAX_CALL_ATTEMPTS:
            return False
        if borrower.next_eligible_at > utc_now():
            return False
        return self._health.should_allow_retry(provider_name)

    def outcome_for_terminal_call(self, answered: bool) -> BorrowerReleaseOutcome:
        if answered:
            return BorrowerReleaseOutcome.CONTACTED
        return BorrowerReleaseOutcome.RETRY
