from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bulletjournal.domain.errors import ArtifactError, GraphValidationError, InvalidRequestError, NotFoundError, RunConflictError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GraphValidationError)
    async def graph_validation_handler(_: Request, exc: GraphValidationError) -> JSONResponse:
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    @app.exception_handler(RunConflictError)
    async def run_conflict_handler(_: Request, exc: RunConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    @app.exception_handler(ArtifactError)
    async def artifact_handler(_: Request, exc: ArtifactError) -> JSONResponse:
        return JSONResponse(status_code=400, content={'detail': str(exc)})

    @app.exception_handler(InvalidRequestError)
    async def invalid_request_handler(_: Request, exc: InvalidRequestError) -> JSONResponse:
        return JSONResponse(status_code=400, content={'detail': str(exc)})

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={'detail': str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def missing_handler(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={'detail': str(exc)})
