from datetime import datetime

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


class AgentRepository(BaseRepository):
    collection_name = COLLECTION_AGENTS

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
        document = await self.collection.find_one_and_update(
            {
                "_id": agent_id,
                "reserved_by": worker_id,
                "state": {"$in": sorted(state.value for state in sources)},
            },
            {
                "$set": {
                    "state": target_state.value,
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
            {
                "$set": {"state": to_state.value, "state_changed_at": now},
                "$inc": {"state_version": 1},
            },
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
