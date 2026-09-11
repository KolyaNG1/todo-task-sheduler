from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from planner.api.router import router
from planner.application.errors import ApplicationError
from planner.bootstrap import build_container
from planner.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    container = build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.initialize()
        app.state.container = container
        app.state.session_factory = container.session_factory
        yield
        container.engine.dispose()

    app = FastAPI(
        title="Планировщик недели — API",
        version="0.1.0",
        description="Версионированный программный интерфейс личного планировщика.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApplicationError)
    async def handle_application_error(_request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(_request: Request, _exc: IntegrityError) -> JSONResponse:
        # Уникальность имени и ссылочная целостность — ошибка ввода, а не 500.
        return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": "Такая запись уже существует или связана с другими данными.", "details": {}}})

    app.include_router(router, prefix="/api/v1", tags=["Планировщик"])
    return app


app = create_app()


def run() -> None:
    uvicorn.run("planner.main:app", host="127.0.0.1", port=8000, reload=True)
