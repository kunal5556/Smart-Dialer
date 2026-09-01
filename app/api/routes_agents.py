from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_api_key, require_campaign
from app.api.errors import ConflictError, NotFoundError
from app.api.schemas import AgentListResponse, AgentRecord
from app.models.agent import Agent
from app.models.campaign import Campaign
from app.models.enums import AgentState
from app.state_machines.agent_sm import TransitionActor

router = APIRouter(prefix="/api", tags=["agents"])

AGENT_ACTIONS = {
    "login": AgentState.AVAILABLE,
    "logout": AgentState.OFFLINE,
    "pause": AgentState.PAUSED,
    "resume": AgentState.AVAILABLE,
}


def to_record(agent: Agent) -> AgentRecord:
    return AgentRecord(
        id=agent.id,
        name=agent.name,
        state=agent.state.value,
        state_version=agent.state_version,
        reserved_by=agent.reserved_by,
        lease_expires_at=agent.lease_expires_at,
        current_call_id=agent.current_call_id,
        last_heartbeat_at=agent.last_heartbeat_at,
        state_changed_at=agent.state_changed_at,
    )


@router.get("/campaigns/{campaign_id}/agents", response_model=AgentListResponse)
async def list_agents(
    request: Request,
    campaign: Campaign = Depends(require_campaign),
) -> AgentListResponse:
    agents_repo = request.app.state.agent_repository
    agents = await agents_repo.find_for_campaign(campaign.id)
    counts = await agents_repo.count_by_state(campaign.id)
    return AgentListResponse(
        campaign_id=campaign.id,
        state_summary={state.value: counts[state] for state in AgentState},
        agents=[to_record(agent) for agent in agents],
    )


@router.post(
    "/agents/{agent_id}/{action}",
    response_model=AgentRecord,
    dependencies=[Depends(require_api_key)],
)
async def agent_action(agent_id: str, action: str, request: Request) -> AgentRecord:
    agents_repo = request.app.state.agent_repository
    agent = await agents_repo.find_by_id(agent_id)
    if agent is None:
        raise NotFoundError("agent", agent_id)

    if action == "heartbeat":
        await agents_repo.heartbeat(agent.id)
        return to_record(await agents_repo.find_by_id(agent.id))

    target = AGENT_ACTIONS.get(action)
    if target is None:
        raise NotFoundError("agent action", action)

    updated = await agents_repo.transition_agent(
        agent_id=agent.id,
        from_state=agent.state,
        to_state=target,
        actor=TransitionActor.AGENT,
        expected_version=agent.state_version,
    )
    if updated is None:
        raise ConflictError(
            f"Agent {agent_id} changed state before the {action} could be applied",
            {"from": agent.state.value, "to": target.value},
        )
    if action == "login":
        await agents_repo.heartbeat(agent.id)
        updated = await agents_repo.find_by_id(agent.id)
    return to_record(updated)
