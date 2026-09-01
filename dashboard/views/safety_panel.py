import pandas as pd
import streamlit as st

from dashboard.api_client import SmartDialerClient
from dashboard.formatting import number, timestamp, verdict


def render(client: SmartDialerClient, campaign_id: str) -> None:
    st.subheader("Safety Controller")
    with st.spinner("Loading safety decisions"):
        decisions = client.get_safety_decisions(campaign_id, limit=10)

    if not decisions:
        st.info("No safety decisions yet — start the campaign to produce some.")
        return

    latest = decisions[0]
    columns = st.columns(3)
    columns[0].metric("Requested", latest["requested"])
    columns[1].metric(
        "Approved",
        latest["approved"],
        delta=latest["approved"] - latest["requested"],
    )
    columns[2].metric("Verdict", verdict(latest["verdict"]))

    if latest["binding_constraint"]:
        st.warning(f"Binding constraint: {latest['binding_constraint']}")
    else:
        st.success("No constraint reduced this request")

    st.caption(
        f"Decision {latest['id'][:8]} at {timestamp(latest['created_at'])} "
        f"(snapshot age {latest['snapshot_age_ms']} ms)"
    )

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Constraint": constraint["name"],
                    "Limit": constraint["limit"],
                    "Observed": number(constraint["value"]),
                    "Binding": "⬅ yes" if constraint["binding"] else "",
                }
                for constraint in latest["constraints"]
            ]
        ),
        width="stretch",
        hide_index=True,
    )
