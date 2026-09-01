from app.models.decisions import PacingDecision, SafetyConstraintRecord
from app.models.decisions import SafetyDecision as SafetyDecisionDocument
from app.repositories.base import (
    COLLECTION_PACING_DECISIONS,
    COLLECTION_SAFETY_DECISIONS,
    BaseRepository,
)
from app.safety.models import PacingRequest, SafetyDecision


class DecisionRepository(BaseRepository):
    collection_name = COLLECTION_SAFETY_DECISIONS

    @property
    def pacing_collection(self):
        return self.database[COLLECTION_PACING_DECISIONS]

    async def record_pacing_request(
        self,
        campaign_id: str,
        request: PacingRequest,
    ) -> PacingDecision:
        decision = PacingDecision(
            campaign_id=campaign_id,
            dialing_mode=request.mode,
            requested=request.requested,
            inputs=request.inputs,
            explanation=request.explanation,
        )
        await self.pacing_collection.insert_one(decision.to_mongo())
        return decision

    async def record_safety_decision(self, decision: SafetyDecision) -> SafetyDecisionDocument:
        document = SafetyDecisionDocument(
            campaign_id=decision.campaign_id,
            pacing_decision_id=decision.pacing_decision_id,
            requested=decision.requested,
            approved=decision.approved,
            verdict=decision.verdict,
            constraints=[
                SafetyConstraintRecord(
                    name=constraint.name,
                    limit=constraint.limit,
                    value=constraint.value,
                    binding=constraint.binding,
                )
                for constraint in decision.constraints
            ],
            binding_constraint=decision.binding_constraint,
            snapshot_age_ms=decision.snapshot_age_ms,
            created_at=decision.created_at,
        )
        await self.collection.insert_one(document.to_mongo())
        return document

    async def find_recent_pacing_decisions(
        self,
        campaign_id: str,
        limit: int,
    ) -> list[PacingDecision]:
        cursor = (
            self.pacing_collection.find({"campaign_id": campaign_id})
            .sort("created_at", -1)
            .limit(limit)
        )
        return [PacingDecision.from_mongo(document) async for document in cursor]

    async def find_recent_safety_decisions(
        self,
        campaign_id: str,
        limit: int,
    ) -> list[SafetyDecisionDocument]:
        cursor = (
            self.collection.find({"campaign_id": campaign_id})
            .sort("created_at", -1)
            .limit(limit)
        )
        return [SafetyDecisionDocument.from_mongo(document) async for document in cursor]
