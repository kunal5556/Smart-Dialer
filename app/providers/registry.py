from app.providers.base import EventCallback, TelecomProvider
from app.providers.errors import ProviderUnavailable
from app.providers.mock_a import MockProviderA
from app.providers.mock_b import MockProviderB


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, TelecomProvider] = {}

    def register(self, provider: TelecomProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> TelecomProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderUnavailable(name)
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    async def shutdown(self) -> None:
        for provider in self._providers.values():
            await provider.shutdown()


def build_registry(on_event: EventCallback, seed: int) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(MockProviderA(on_event=on_event, seed=seed))
    registry.register(MockProviderB(on_event=on_event, seed=seed))
    return registry
