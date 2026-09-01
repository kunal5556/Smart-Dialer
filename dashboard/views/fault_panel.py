import streamlit as st

from dashboard.api_client import ApiError, SmartDialerClient

FAULTS = [
    ("provider_outage", "Force a provider outage"),
    ("provider_latency_spike", "Spike provider latency"),
    ("duplicate_event_burst", "Replay real events as duplicates"),
    ("out_of_order_burst", "Replay real events out of order"),
    ("agent_availability_drop", "Take agents offline"),
]


def render(client: SmartDialerClient, campaign_id: str) -> None:
    st.subheader("Fault injection")
    st.caption("Every button below drives a real mechanism in the backend, not a simulation of one.")

    confirmed = st.checkbox("I understand this disrupts the running demo")

    fault = st.selectbox("Fault", [name for name, _ in FAULTS], format_func=_label)
    provider_name = st.selectbox("Provider", ["mock_b", "mock_a"], key="fault_provider")
    seconds = st.slider("Outage length (seconds)", 0, 120, 30, key="fault_seconds")
    agents_offline = st.slider("Agents to take offline", 1, 100, 5, key="fault_agents")

    if st.button("Inject fault", disabled=not confirmed, width="stretch"):
        payload = {
            "fault": fault,
            "provider_name": provider_name,
            "seconds": float(seconds),
            "agents_offline": int(agents_offline),
            "campaign_id": campaign_id,
        }
        try:
            result = client.inject_fault(payload)
        except ApiError as error:
            st.error(error.message)
        else:
            st.success(f"{result['fault']}: {result['detail']} (affected {result['affected']})")


def _label(fault: str) -> str:
    for name, label in FAULTS:
        if name == fault:
            return label
    return fault
