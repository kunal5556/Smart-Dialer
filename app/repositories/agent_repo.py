from datetime import datetime
from typing import Any

from pymongo import ASCENDING, ReturnDocument

from app.models.agent import Agent
from app.models.enums import AgentState
from app.repositories.base import (
    COLLECTION_AGENTS,
    BaseRepository,
    build_lease_fields,
    candidate_window,
    cleared_lease_fields,
)
from app.state_machines.agent_sm import (
    TransitionActor,
    allowed_sources,
    validate_transition,
)


TIME_ACCOUNTING_FIELDS: dict[AgentState, tuple[str, ...]] = {
    AgentState.AVAILABLE: ("available_time_ms",),
    AgentState.RESERVED: ("busy_time_ms",),
    AgentState.DIALING: ("busy_time_ms",),
    AgentState.CONNECTED: ("busy_time_ms", "connected_time_ms"),
    AgentState.WRAP_UP: ("busy_time_ms", "wrap_up_time_ms"),
}


def time_accounting_updates(from_state: AgentState, now: datetime) -> dict[str, Any]:
    fields = TIME_ACCOUNTING_FIELDS.get(from_state, ())
    elapsed = {
        "$max": [0, {"$subtract": [now, {"$ifNull": ["$state_changed_at", now]}]}]
    }
    return {field: {"$add": [f"${field}", elapsed]} for field in fields}


class AgentRepository(BaseRepository):
    collection_name = COLLECTION_AGENTS

    async def insert_many(self, records: list[Agent]) -> int:
        if not records:
            return 0
        await self.collection.insert_many(record.to_mongo() for record in records)
        return len(records)

    async def try_reserve_agent(
        self,
        campaign_id: str,
        agent_id: str,
        worker_id: str,
        ttl_seconds: int,
    ) -> Agent | None:
        now = self.now()
        document = await self.collection.find_one_and_update(
            {
                "_id": agent_id,
                "campaign_id": campaign_id,
                "state": AgentState.AVAILABLE.value,
            },
            {
                "$set": {
                    "state": AgentState.RESERVED.value,
                    "state_changed_at": now,
                    **build_lease_fields(worker_id, ttl_seconds, now),
                },
                "$inc": {"state_version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Agent.from_mongo(document)

    async def find_claimable_agents(self, campaign_id: str, needed: int) -> list[Agent]:
        window = candidate_window(needed)
        if window == 0:
            return []
        cursor = (
            self.collection.find(
                {"campaign_id": campaign_id, "state": AgentState.AVAILABLE.value}
            )
            .sort("state_changed_at", ASCENDING)
            .limit(window)
        )
        return [Agent.from_mongo(document) async for document in cursor]

    async def release_agent(
        self,
        agent_id: str,
        worker_id: str,
        target_state: AgentState,
        actor: TransitionActor,
    ) -> Agent | None:
        sources = allowed_sources(target_state, actor)
        if not sources:
            return None
        now = self.now()
        accounting = {
            source: time_accounting_updates(source, now) for source in sources
        }
        document = await self.collection.find_one_and_update(
            {
                "_id": agent_id,
                "reserved_by": worker_id,
                "state": {"$in": sorted(state.value for state in sources)},
            },
            [
                {
                    "$set": {
                        **self._conditional_accounting(accounting),
                        "state": target_state.value,
                        "state_changed_at": now,
                        "current_call_id": None,
                        "state_version": {"$add": ["$state_version", 1]},
                        **cleared_lease_fields(),
                    }
                }
            ],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Agent.from_mongo(document)

    @staticmethod
    def _conditional_accounting(
        accounting: dict[AgentState, dict[str, Any]],
    ) -> dict[str, Any]:
        fields = {field for updates in accounting.values() for field in updates}
        result: dict[str, Any] = {}
        for field in sorted(fields):
            branches = [
                {"case": {"$eq": ["$state", state.value]}, "then": updates[field]}
                for state, updates in accounting.items()
                if field in updates
            ]
            result[field] = {"$switch": {"branches": branches, "default": f"${field}"}}
        return result

    async def transition_agent(
        self,
        agent_id: str,
        from_state: AgentState,
        to_state: AgentState,
        actor: TransitionActor,
        expected_version: int,
    ) -> Agent | None:
        validate_transition(from_state, to_state, actor)
        now = self.now()
        document = await self.collection.find_one_and_update(
            {
                "_id": agent_id,
                "state": from_state.value,
                "state_version": expected_version,
            },
            [
                {
                    "$set": {
                        "state": to_state.value,
                        "state_changed_at": now,
                        "state_version": {"$add": ["$state_version", 1]},
                        **time_accounting_updates(from_state, now),
                    }
                }
            ],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Agent.from_mongo(document)

    async def count_by_state(self, campaign_id: str) -> dict[AgentState, int]:
        pipeline = [
            {"$match": {"campaign_id": campaign_id}},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]
        counts = {state: 0 for state in AgentState}
        async for row in self.collection.aggregate(pipeline):
            counts[AgentState(row["_id"])] = row["count"]
        return counts

    async def find_expired_agent_leases(self, now: datetime, limit: int) -> list[Agent]:
        sources = allowed_sources(AgentState.AVAILABLE, TransitionActor.RECOVERY)
        cursor = self.collection.find(
            {
                "state": {"$in": sorted(state.value for state in sources)},
                "reserved_by": {"$ne": None},
                "lease_expires_at": {"$lt": now},
            }
        ).limit(limit)
        return [Agent.from_mongo(document) async for document in cursor]

    async def release_expired_agent_lease(self, agent: Agent, now: datetime) -> Agent | None:
        document = await self.collection.find_one_and_update(
            {
                "_id": agent.id,
                "state": agent.state.value,
                "state_version": agent.state_version,
                "lease_expires_at": {"$lt": now},
            },
            {
                "$set": {
                    "state": AgentState.AVAILABLE.value,
                    "state_changed_at": now,
                    "current_call_id": None,
                    **cleared_lease_fields(),
                },
                "$inc": {"state_version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Agent.from_mongo(document)

    async def reclaim_expired_agent_leases(self, now: datetime, limit: int) -> list[Agent]:
        reclaimed: list[Agent] = []
        for candidate in await self.find_expired_agent_leases(now, limit):
            released = await self.release_expired_agent_lease(candidate, now)
            if released is not None:
                reclaimed.append(released)
        return reclaimed

    async def find_heartbeat_expired(self, older_than: datetime, limit: int) -> list[Agent]:
        cursor = self.collection.find(
            {
                "state": {"$ne": AgentState.OFFLINE.value},
                "last_heartbeat_at": {"$ne": None, "$lt": older_than},
            }
        ).limit(limit)
        return [Agent.from_mongo(document) async for document in cursor]

    async def find_expired_wrap_ups(
        self,
        older_than: datetime,
        limit: int,
        campaign_id: str | None = None,
    ) -> list[Agent]:
        query = {
            "state": AgentState.WRAP_UP.value,
            "state_changed_at": {"$lte": older_than},
        }
        if campaign_id is not None:
            query["campaign_id"] = campaign_id
        cursor = self.collection.find(query).sort("state_changed_at", ASCENDING).limit(limit)
        return [Agent.from_mongo(document) async for document in cursor]

    async def count_expired_leases(self, now: datetime) -> int:
        sources = allowed_sources(AgentState.AVAILABLE, TransitionActor.RECOVERY)
        return await self.collection.count_documents(
            {
                "state": {"$in": sorted(state.value for state in sources)},
                "reserved_by": {"$ne": None},
                "lease_expires_at": {"$lt": now},
            }
        )

    async def count_connected_longer_than(self, campaign_id: str, older_than: datetime) -> int:
        return await self.collection.count_documents(
            {
                "campaign_id": campaign_id,
                "state": AgentState.CONNECTED.value,
                "state_changed_at": {"$lte": older_than},
            }
        )

    async def find_for_campaign(self, campaign_id: str, limit: int = 10000) -> list[Agent]:
        cursor = self.collection.find({"campaign_id": campaign_id}).limit(limit)
        return [Agent.from_mongo(document) async for document in cursor]

    async def find_by_id(self, agent_id: str) -> Agent | None:
        document = await self.collection.find_one({"_id": agent_id})
        if document is None:
            return None
        return Agent.from_mongo(document)

    async def heartbeat(self, agent_id: str) -> bool:
        result = await self.collection.update_one(
            {"_id": agent_id},
            {"$set": {"last_heartbeat_at": self.now()}},
        )
        return result.matched_count == 1
