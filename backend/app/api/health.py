from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLTimeoutError

from app.persistence.database import check_database_connection

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=None)
def health(request: Request, response: Response) -> dict[str, str] | JSONResponse:
    request_id = request.state.request_id
    try:
        check_database_connection()
    except (OperationalError, InterfaceError, SQLTimeoutError):
        return JSONResponse(
            status_code=503,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "database_failed",
                    "message": "Database unavailable",
                    "stage": "health",
                    "request_id": request_id,
                    "details": {},
                }
            },
        )
    response.headers["X-Request-ID"] = request_id
    return {"status": "ok", "database": "connected"}
