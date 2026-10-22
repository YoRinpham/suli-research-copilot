import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, DisconnectionError, TimeoutError as SQLTimeoutError
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.health import router as health_router
from app.api.papers import router as papers_router
from app.api.projects import router as projects_router
from app.api.evidence import router as evidence_router
from app.api.retrieval import router as retrieval_router
from app.api.scientific import router as scientific_router
from app.api.m4 import router as m4_router
from app.api.m5 import router as m5_router
from app.api.m6 import router as m6_router, benchmark_router
from app.config.settings import Settings, get_settings
from app.services.errors import ServiceError

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str, stage: str, request_id: str, details: dict[str, object] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers={"X-Request-ID": request_id},
        content={"error": {
            "code": code,
            "message": message,
            "stage": stage,
            "request_id": request_id,
            "details": details or {},
        }},
    )


class BodyTooLarge(Exception):
    pass


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, body_limit_bytes: int):
        self.app = app
        self.body_limit_bytes = body_limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("X-Request-ID") or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        response_started = False
        rejected = False

        async def send_with_id(message: Message) -> None:
            nonlocal response_started
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        async def reject() -> None:
            response = error_response(413, "file_too_large", "Request body exceeds the upload limit", "paper_upload", request_id)
            await response(scope, receive, send)

        try:
            content_length = int(headers.get("Content-Length", ""))
        except ValueError:
            content_length = None
        if content_length is not None and content_length > self.body_limit_bytes:
            await reject()
            return

        received = 0

        async def bounded_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.body_limit_bytes:
                    if response_started:
                        raise BodyTooLarge()
                    rejected = True
                    await reject()
                    return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, bounded_receive, send_with_id)
        except Exception:
            if not rejected:
                raise


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(title="溯理 SULI API")
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.cors_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )

    application.add_middleware(RequestContextMiddleware, body_limit_bytes=settings.request_body_limit_mb * 1024 * 1024)

    @application.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return error_response(exc.status_code, exc.code, exc.message, exc.stage, request.state.request_id, exc.details)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "validation_error", "Request validation failed", "request", request.state.request_id)

    @application.exception_handler(DBAPIError)
    @application.exception_handler(DisconnectionError)
    @application.exception_handler(SQLTimeoutError)
    async def database_error(request: Request, exc: Exception):
        logger.error("Database failure request_id=%s type=%s", request.state.request_id, type(exc).__name__)
        return error_response(500, "database_failed", "Database operation failed", "database", request.state.request_id)

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.exception("Unexpected application failure request_id=%s", request.state.request_id)
        return error_response(500, "internal_error", "Internal server error", "request", request.state.request_id)

    application.include_router(health_router)
    application.include_router(projects_router)
    application.include_router(papers_router)
    application.include_router(evidence_router)
    application.include_router(retrieval_router)
    application.include_router(scientific_router)
    application.include_router(m4_router)
    application.include_router(m5_router)
    application.include_router(m6_router)
    application.include_router(benchmark_router)
    return application


app = create_app()
