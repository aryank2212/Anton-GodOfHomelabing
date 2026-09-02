from app.api.routes.devices import router as devices_router
from app.api.routes.health import router as health_router
from app.api.routes.observations import router as observations_router
from app.api.routes.observers import router as observers_router
from app.api.routes.presence import router as presence_router
from app.api.routes.situations import router as situations_router

__all__ = [
    "devices_router",
    "health_router",
    "observations_router",
    "observers_router",
    "presence_router",
    "situations_router",
]
