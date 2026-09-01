import pandas as pd
import streamlit as st

from dashboard.api_client import SmartDialerClient
from dashboard.formatting import call_state, duration, event_status, timestamp

CALL_STATES = [
    "QUEUED",
    "RESERVED",
    "INITIATED",
    "RINGING",
    "ANSWERED",
    "CONNECTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]


def render(client: SmartDialerClient, campaign_id: str) -> None:
    st.subheader("Calls")
    state_filter = st.selectbox("Filter by state", ["All"] + CALL_STATES, index=0)
    selected_state = None if state_filter == "All" else state_filter

    with st.spinner("Loading calls"):
        payload = client.get_calls(campaign_id, state=selected_state)

    calls = payload["calls"]
    if not calls:
        st.info("No calls yet — start the campaign to place some.")
        return

    frame = pd.DataFrame(
        [
            {
                "Call": call["id"][:8],
                "State": call_state(call["state"]),
                "Provider": call["provider_name"],
                "Attempt": call["attempt"],
                "Duration": duration(call["duration_seconds"]),
                "Failure reason": call["failure_reason"] or "—",
                "Created": timestamp(call["created_at"]),
            }
            for call in calls
        ]
    )
    st.dataframe(frame, width="stretch", hide_index=True)

    st.divider()
    st.caption("Provider event trail — this is where duplicate and out-of-order handling is visible")
    options = {f"{call['id'][:8]} ({call['state']})": call["id"] for call in calls}
    chosen = st.selectbox("Inspect a call", list(options))
    detail = client.get_call_detail(options[chosen])

    events = detail["events"]
    if not events:
        st.info("This call has no provider events yet.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Event": event["event_type"],
                    "Processing": event_status(event["processing_status"]),
                    "Applied transition": event["applied_transition"] or "—",
                    "Received": timestamp(event["received_at"]),
                }
                for event in events
            ]
        ),
        width="stretch",
        hide_index=True,
    )
