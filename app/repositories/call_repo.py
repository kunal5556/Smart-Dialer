from datetime import datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.models.call import Call, build_idempotency_key
from app.models.enums import CallState
from app.repositories.base import COLLECTION_CALLS, BaseRepository
from app.state_machines.call_sm import is_terminal, rank

STATE_TIMESTAMP_FIELDS: dict[CallState, str] = {
    CallState.INITIATED: "initiated_at",
    CallState.RINGING: "ringing_at",
    CallState.ANSWERED: "answered_at",
    CallState.CONNECTED: "connected_at",
}


class CallRepository(BaseRepository):
    collection_name = COLLECTION_CALLS

    async def create_call(
        self,
        campaign_id: str,
        agent_id: str,
        borrower_id: str,
        provider_name: str,
        worker_id: str,
        attempt: int = 1,
        retry_of_call_id: str | None = None,
    ) -> Call:
        call = Call(
            campaign_id=campaign_id,
            agent_id=agent_id,
            borrower_id=borrower_id,
            provider_name=provider_name,
            created_by_worker=worker_id,
            attempt=attempt,
            retry_of_call_id=retry_of_call_id,
            idempotency_key=build_idempotency_key(campaign_id, agent_id, borrower_id, attempt),
        )
        try:
            await self.collection.insert_one(call.to_mongo())
        except DuplicateKeyError:
            existing = await self.collection.find_one(
                {"idempotency_key": call.idempotency_key}
            )
            return Call.from_mongo(existing)
        return call

    async def find_by_id(self, call_id: str) -> Call | None:
        document = await self.collection.find_one({"_id": call_id})
        if document is None:
            return None
        return Call.from_mongo(document)

    async def find_by_provider_call_id(
        self,
        provider_name: str,
        provider_call_id: str,
    ) -> Call | None:
        document = await self.collection.find_one(
            {"provider_name": provider_name, "provider_call_id": provider_call_id}
        )
        if document is None:
            return None
        return Call.from_mongo(document)

    async def attach_provider_call_id(self, call_id: str, provider_call_id: str) -> Call | None:
        try:
            document = await self.collection.find_one_and_update(
                {"_id": call_id, "provider_call_id": None},
                {"$set": {"provider_call_id": provider_call_id, "updated_at": self.now()}},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return None
        if document is None:
            return None
        return Call.from_mongo(document)

    async def transition_call(
        self,
        call_id: str,
        target_state: CallState,
        failure_reason: str | None = None,
    ) -> Call | None:
        target_rank = rank(target_state)
        now = self.now()
        updates = {
            "state": target_state.value,
            "state_rank": target_rank,
            "terminal": is_terminal(target_state),
            "updated_at": now,
        }
        timestamp_field = STATE_TIMESTAMP_FIELDS.get(target_state)
        if timestamp_field is not None:
            updates[timestamp_field] = now
        if is_terminal(target_state):
            updates["ended_at"] = now
        if failure_reason is not None:
            updates["failure_reason"] = failure_reason

        document = await self.collection.find_one_and_update(
            {"_id": call_id, "terminal": False, "state_rank": {"$lt": target_rank}},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Call.from_mongo(document)

    async def find_stale_calls(self, older_than: datetime, limit: int) -> list[Call]:
        cursor = (
            self.collection.find({"terminal": False, "updated_at": {"$lt": older_than}})
            .sort("updated_at", 1)
            .limit(limit)
        )
        return [Call.from_mongo(document) async for document in cursor]

    async def outcome_counts_between(
        self,
        campaign_id: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, int]:
        pipeline = [
            {
                "$match": {
                    "campaign_id": campaign_id,
                    "terminal": True,
                    "ended_at": {"$gte": start, "$lt": end},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "answered": {
                        "$sum": {"$cond": [{"$ne": ["$answered_at", None]}, 1, 0]}
                    },
                    "failed": {
                        "$sum": {
                            "$cond": [{"$eq": ["$state", CallState.FAILED.value]}, 1, 0]
                        }
                    },
                }
            },
        ]
        counts = {"total": 0, "answered": 0, "failed": 0}
        async for row in self.collection.aggregate(pipeline):
            counts["total"] = row["total"]
            counts["answered"] = row["answered"]
            counts["failed"] = row["failed"]
        return counts

    async def average_talk_time_seconds(self, campaign_id: str) -> float:
        pipeline = [
            {
                "$match": {
                    "campaign_id": campaign_id,
                    "answered_at": {"$ne": None},
                    "ended_at": {"$ne": None},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "average": {
                        "$avg": {"$subtract": ["$ended_at", "$answered_at"]}
                    },
                }
            },
        ]
        async for row in self.collection.aggregate(pipeline):
            if row["average"] is not None:
                return float(row["average"]) / 1000.0
        return 0.0

    async def average_setup_time_ms(self, campaign_id: str) -> float:
        pipeline = [
            {
                "$match": {
                    "campaign_id": campaign_id,
                    "initiated_at": {"$ne": None},
                    "ringing_at": {"$ne": None},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "average": {
                        "$avg": {"$subtract": ["$ringing_at", "$initiated_at"]}
                    },
                }
            },
        ]
        async for row in self.collection.aggregate(pipeline):
            if row["average"] is not None:
                return float(row["average"])
        return 0.0

    async def count_by_state(self, campaign_id: str) -> dict[CallState, int]:
        pipeline = [
            {"$match": {"campaign_id": campaign_id}},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]
        counts = {state: 0 for state in CallState}
        async for row in self.collection.aggregate(pipeline):
            counts[CallState(row["_id"])] = row["count"]
        return counts
