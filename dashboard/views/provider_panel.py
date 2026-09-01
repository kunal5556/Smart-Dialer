import pandas as pd
import streamlit as st

from dashboard.api_client import ApiError, SmartDialerClient
from dashboard.formatting import milliseconds, percentage, provider_status


def render(client: SmartDialerClient) -> None:
    st.subheader("Provider health")
    with st.spinner("Loading provider health"):
        providers = client.get_provider_health()

    if not providers:
        st.info("No providers are registered.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Provider": provider["provider_name"],
                    "Status": provider_status(provider["status"]),
                    "Requests": provider["request_count"],
                    "Failure rate": percentage(provider["failure_rate"]),
                    "Timeout rate": percentage(provider["timeout_rate"]),
                    "p95 latency": milliseconds(provider["p95_latency_ms"]),
                    "Consecutive failures": provider["consecutive_failures"],
                    "Low confidence": "yes" if provider["low_confidence"] else "no",
                }
                for provider in providers
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.divider()
    st.caption("Outage control")
    names = [provider["provider_name"] for provider in providers]
    chosen = st.selectbox("Provider", names, key="outage_provider")
    seconds = st.slider("Outage length (seconds)", 0, 120, 30, key="outage_seconds")

    columns = st.columns(2)
    if columns[0].button("Force outage", width="stretch"):
        _apply(client, chosen, float(seconds))
    if columns[1].button("Clear outage", width="stretch"):
        _apply(client, chosen, 0.0)


def _apply(client: SmartDialerClient, provider_name: str, seconds: float) -> None:
    try:
        client.set_provider_outage(provider_name, seconds)
    except ApiError as error:
        st.error(error.message)
        return
    st.success(f"Outage set to {seconds:g}s on {provider_name}")
    st.rerun()
