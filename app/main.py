"""
# FastAPI application factory — THE MAIN FILE
# Creates the app with:
#   - Lifespan handler (startup: register providers, shutdown: close DB/Redis)
#   - CORS middleware
#   - Custom exception handler for VoiceAgentError
#   - 8 API routers: auth, agents, calls, campaigns, tenants, dashboard, exports, webhooks
#   - 1 WebSocket router: /ws/media-stream/{tenant}/{agent}
#   - Provider registration: imports all 7 providers to register them
# Run: uvicorn app.main:app --reload
"""
"""
Main FastAPI application entry point.

Assembles all routers, middleware, and lifecycle management.
Run with: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations
 
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
 
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
 
from app.config import get_settings
from app.core.exceptions import VoiceAgentError
from app.db.session import close_db, close_redis
 
logger = logging.getLogger(__name__)
 
 
def configure_logging() -> None:
    """Configure structured logging with structlog."""
    settings = get_settings()
 
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.is_development
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
 
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
 
 
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle management."""
    configure_logging()
    logger.info("Starting Voice Agent Platform...")
 
    # Import providers to register them in the registry
    import app.voice.providers.stt.deepgram_provider  # noqa: F401
    import app.voice.providers.llm.openai_provider  # noqa: F401
    import app.voice.providers.llm.anthropic_provider  # noqa: F401
    import app.voice.providers.tts.cartesia_provider  # noqa: F401
    import app.voice.providers.tts.elevenlabs_provider  # noqa: F401
    import app.voice.providers.telephony.twilio_provider  # noqa: F401
    import app.voice.providers.telephony.plivo_provider  # noqa: F401
 
    logger.info("Providers registered. Platform ready.")
 
    yield
 
    # Shutdown
    logger.info("Shutting down...")
    await close_db()
    await close_redis()
    logger.info("Shutdown complete.")
 
 
def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI app."""
    settings = get_settings()
 
    app = FastAPI(
        title="Voice Agent Platform API",
        description="Production-ready AI Voice Calling Agent SaaS",
        version="0.1.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        lifespan=lifespan,
    )
 
    # --- Middleware ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
 
    # --- Exception handlers ---
    @app.exception_handler(VoiceAgentError)
    async def voice_agent_error_handler(request: Request, exc: VoiceAgentError) -> JSONResponse:
        status_map = {
            "AUTH_ERROR": 401,
            "FORBIDDEN": 403,
            "TENANT_NOT_FOUND": 404,
            "CALL_NOT_FOUND": 404,
            "QUOTA_EXCEEDED": 429,
            "CONCURRENT_LIMIT": 429,
            "COMPLIANCE_ERROR": 403,
        }
        status_code = status_map.get(exc.code, 500)
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.code, "message": exc.message},
        )
 
    # --- Routes ---
    from app.api.v1.health import router as health_router
    from app.api.v1.auth import router as auth_router
    from app.api.v1.calls import router as calls_router
    from app.api.v1.agents import router as agents_router
    from app.api.v1.tenants import router as tenants_router
    from app.api.v1.campaigns import router as campaigns_router
    from app.api.v1.dashboard import router as dashboard_router
    from app.api.v1.exports import router as exports_router
    from app.api.v1.webhooks import router as webhook_router, ws_router
 
    # Health checks at root
    app.include_router(health_router)
 
    # API v1 routes
    prefix = settings.api_v1_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(calls_router, prefix=prefix)
    app.include_router(agents_router, prefix=prefix)
    app.include_router(tenants_router, prefix=prefix)
    app.include_router(campaigns_router, prefix=prefix)
    app.include_router(dashboard_router, prefix=prefix)
    app.include_router(exports_router, prefix=prefix)
    app.include_router(webhook_router, prefix=prefix)
 
    # WebSocket routes (no prefix — Twilio needs a clean URL)
    app.include_router(ws_router)
 
    return app
 
 
# The ASGI application instance
app = create_app()