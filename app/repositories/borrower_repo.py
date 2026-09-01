from datetime import datetime
from enum import Enum
from typing import Any

from pymongo import ASCENDING, ReturnDocument

from app.models.borrower import Borrower
from app.models.enums import BorrowerStatus
from app.repositories.base import (
    COLLECTION_BORROWERS,
    BaseRepository,
    build_lease_fields,
    candidate_window,
    cleared_lease_fields,
)


class BorrowerReleaseOutcome(str, Enum):
    RELEASED = "RELEASED"
    RETRY = "RETRY"
    CONTACTED = "CONTACTED"


RELEASABLE_STATUSES = frozenset({BorrowerStatus.RESERVED, BorrowerStatus.IN_CALL})


class BorrowerRepository(BaseRepository):
    collection_name = COLLECTION_BORROWERS

    async def try_reserve_borrower(
        self,
        campaign_id: str,
        borrower_id: str,
        worker_id: str,
        ttl_seconds: int,
    ) -> Borrower | None:
        now = self.now()
        document = await self.collection.find_one_and_update(
            {
                "_id": borrower_id,
                "campaign_id": campaign_id,
                "status": BorrowerStatus.PENDING.value,
                "next_eligible_at": {"$lte": now},
            },
            {
                "$set": {
                    "status": BorrowerStatus.RESERVED.value,
                    **build_lease_fields(worker_id, ttl_seconds, now),
                },
                "$inc": {"state_version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Borrower.from_mongo(document)

    async def find_claimable_borrowers(self, campaign_id: str, needed: int) -> list[Borrower]:
        window = candidate_window(needed)
        if window == 0:
            return []
        cursor = (
            self.collection.find(
                {
                    "campaign_id": campaign_id,
                    "status": BorrowerStatus.PENDING.value,
                    "next_eligible_at": {"$lte": self.now()},
                }
            )
            .sort("next_eligible_at", ASCENDING)
            .limit(window)
        )
        return [Borrower.from_mongo(document) async for document in cursor]

    async def release_borrower(
        self,
        borrower_id: str,
        worker_id: str,
        outcome: BorrowerReleaseOutcome,
        max_attempts: int,
        backoff_base_seconds: int,
    ) -> Borrower | None:
        now = self.now()
        if outcome is BorrowerReleaseOutcome.RELEASED:
            update = {
                "$set": {"status": BorrowerStatus.PENDING.value, **cleared_lease_fields()},
                "$inc": {"state_version": 1},
            }
        elif outcome is BorrowerReleaseOutcome.CONTACTED:
            update = {
                "$set": {
                    "status": BorrowerStatus.CONTACTED.value,
                    "last_attempt_at": now,
                    **cleared_lease_fields(),
                },
                "$inc": {"state_version": 1, "attempt_count": 1},
            }
        else:
            update = self._build_retry_update(now, max_attempts, backoff_base_seconds)

        document = await self.collection.find_one_and_update(
            {
                "_id": borrower_id,
                "reserved_by": worker_id,
                "status": {"$in": sorted(status.value for status in RELEASABLE_STATUSES)},
            },
            update,
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Borrower.from_mongo(document)

    def _build_retry_update(
        self,
        now: datetime,
        max_attempts: int,
        backoff_base_seconds: int,
    ) -> list[dict[str, Any]]:
        exhausted_status = BorrowerStatus.EXHAUSTED.value
        pending_status = BorrowerStatus.PENDING.value
        return [
            {
                "$set": {
                    "attempt_count": {"$add": ["$attempt_count", 1]},
                    "last_attempt_at": now,
                    "state_version": {"$add": ["$state_version", 1]},
                    "reserved_by": None,
                    "reserved_at": None,
                    "lease_expires_at": None,
                }
            },
            {
                "$set": {
                    "status": {
                        "$cond": [
                            {"$gte": ["$attempt_count", max_attempts]},
                            exhausted_status,
                            pending_status,
                        ]
                    },
                    "next_eligible_at": {
                        "$dateAdd": {
                            "startDate": now,
                            "unit": "second",
                            "amount": {
                                "$multiply": [
                                    backoff_base_seconds,
                                    {"$pow": [2, {"$subtract": ["$attempt_count", 1]}]},
                                ]
                            },
                        }
                    },
                }
            },
        ]

    async def reclaim_expired_borrower_leases(
        self,
        now: datetime,
        limit: int,
    ) -> list[Borrower]:
        cursor = self.collection.find(
            {
                "status": {"$in": sorted(status.value for status in RELEASABLE_STATUSES)},
                "reserved_by": {"$ne": None},
                "lease_expires_at": {"$lt": now},
            }
        ).limit(limit)
        candidates = [document async for document in cursor]

        reclaimed: list[Borrower] = []
        for candidate in candidates:
            document = await self.collection.find_one_and_update(
                {
                    "_id": candidate["_id"],
                    "status": candidate["status"],
                    "state_version": candidate["state_version"],
                    "lease_expires_at": {"$lt": now},
                },
                {
                    "$set": {
                        "status": BorrowerStatus.PENDING.value,
                        "next_eligible_at": now,
                        **cleared_lease_fields(),
                    },
                    "$inc": {"state_version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if document is not None:
                reclaimed.append(Borrower.from_mongo(document))
        return reclaimed
