from .auth import router as auth_router
from .bots import router as bots_router
from .logs import router as logs_router
from .frontend import router as frontend_router
from .webhook import router as webhook_router, public_router
from .backup import router as backup_router
from .ai import router as ai_router
from .tg_proxy import router as tg_proxy_router

__all__ = ["auth_router", "bots_router", "logs_router", "frontend_router", "webhook_router", "backup_router", "ai_router", "tg_proxy_router", "public_router"]
