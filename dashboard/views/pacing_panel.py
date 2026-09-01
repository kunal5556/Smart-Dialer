import pandas as pd
import streamlit as st

from dashboard.api_client import SmartDialerClient
from dashboard.formatting import number, percentage, timestamp

HIGHLIGHT_FIELDS = [
    ("available_agents", "Available agents"),
    ("soon_free_agents", "Soon-free agents"),
    ("free_capacity", "Free capacity"),
    ("effective_answer_rate", "Estimated answer rate"),
    ("calls_needed", "Calls needed"),
    ("in_flight", "In flight"),
    ("raw_request", "Raw request"),
    ("safety_margin", "Safety margin"),
    ("health_factor", "Health factor"),
    ("volatility_factor", "Volatility factor"),
    ("requested", "Requested"),
]


def render(client: SmartDialerClient, campaign_id: str) -> None:
    st.subheader("Why did it ask for that many calls?")
    with st.spinner("Loading pacing decisions"):
        decisions = client.get_pacing_decisions(campaign_id, limit=10)

    if not decisions:
        st.info("No pacing decisions yet — start the campaign to produce some.")
        return

    latest = decisions[0]
    st.metric("Requested this tick", latest["requested"])
    st.info(latest["explanation"])
    st.caption(f"Decision {latest['id'][:8]} at {timestamp(latest['created_at'])}")

    inputs = latest["inputs"]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Input": label,
                    "Value": (
                        percentage(inputs.get(key))
                        if key == "effective_answer_rate"
                        else number(inputs.get(key))
                    ),
                }
                for key, label in HIGHLIGHT_FIELDS
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Every captured input"):
        st.json(inputs)
