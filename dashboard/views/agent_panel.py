import pandas as pd
import streamlit as st

from dashboard.api_client import SmartDialerClient
from dashboard.formatting import agent_state, timestamp

STATE_ORDER = [
    "AVAILABLE",
    "RESERVED",
    "DIALING",
    "CONNECTED",
    "WRAP_UP",
    "PAUSED",
    "OFFLINE",
]


def render(client: SmartDialerClient, campaign_id: str) -> None:
    st.subheader("Agents")
    with st.spinner("Loading agents"):
        payload = client.get_agents(campaign_id)

    summary = payload["state_summary"]
    columns = st.columns(len(STATE_ORDER))
    for column, state in zip(columns, STATE_ORDER):
        column.metric(state.replace("_", " ").title(), summary.get(state, 0))

    agents = payload["agents"]
    if not agents:
        st.info("No agents yet. Seed the campaign to create some.")
        return

    frame = pd.DataFrame(
        [
            {
                "Agent": agent["name"],
                "State": agent_state(agent["state"]),
                "Current call": agent["current_call_id"] or "—",
                "Reserved by": agent["reserved_by"] or "—",
                "Lease expires": timestamp(agent["lease_expires_at"]),
                "Last heartbeat": timestamp(agent["last_heartbeat_at"]),
            }
            for agent in agents
        ]
    )
    st.dataframe(frame, width="stretch", hide_index=True)
