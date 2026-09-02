from app.api.routes.health import router as health_router
from app.api.routes.incidents import router as incidents_router
from app.api.routes.maintenance import router as maintenance_router
from app.api.routes.recovery import router as recovery_router
from app.api.routes.statistics import router as statistics_router

__all__ = [
    "health_router",
    "incidents_router",
    "maintenance_router",
    "recovery_router",
    "statistics_router",
]
