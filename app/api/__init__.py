from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.routes_agents import router as agents_router
from app.api.routes_calls import router as calls_router
from app.api.routes_campaigns import router as campaigns_router
from app.api.routes_decisions import router as decisions_router
from app.api.routes_metrics import router as metrics_router
from app.api.routes_providers import router as providers_router
from app.api.routes_simulation import router as simulation_router

ROUTERS = (
    campaigns_router,
    agents_router,
    calls_router,
    decisions_router,
    metrics_router,
    providers_router,
    simulation_router,
)


def register_api(app: FastAPI) -> None:
    register_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
