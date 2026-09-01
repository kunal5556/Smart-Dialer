class ProviderError(Exception):
    pass


class ProviderTimeout(ProviderError):
    def __init__(self, provider_name: str, call_id: str, timeout_seconds: float) -> None:
        self.provider_name = provider_name
        self.call_id = call_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Provider {provider_name} did not respond for call {call_id} "
            f"within {timeout_seconds} seconds"
        )


class ProviderRejected(ProviderError):
    def __init__(self, provider_name: str, reason: str) -> None:
        self.provider_name = provider_name
        self.reason = reason
        super().__init__(f"Provider {provider_name} rejected the request: {reason}")


class ProviderUnavailable(ProviderError):
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"No telecom provider is registered under the name {provider_name}")
