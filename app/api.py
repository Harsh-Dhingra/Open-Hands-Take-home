"""FastAPI routes. Translate HTTP <-> engine; all rules stay in app.engine."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import engine
from app.models import (
    ErrorOut,
    GameOut,
    GameSummary,
    MoveOut,
    MoveRequest,
    game_out,
    move_out,
)
from app.store import GameNotFound, GameRepository, SqliteRepository

STATIC_DIR = Path(__file__).parent / "static"

STATUS_BY_CODE = {
    GameNotFound.code: 404,
    engine.GameOver.code: 409,
    engine.CellTaken.code: 409,
    engine.NotYourTurn.code: 409,
    engine.OutOfBounds.code: 422,
    engine.InvalidConfig.code: 422,
}

ERROR_RESPONSES = {
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
    422: {"model": ErrorOut},
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(repo: GameRepository | None = None) -> FastAPI:
    repo = repo or SqliteRepository(os.environ.get("DB_PATH", "tictactoe.db"))
    app = FastAPI(title="Tic-Tac-Toe")

    @app.exception_handler(engine.EngineError)
    @app.exception_handler(GameNotFound)
    async def domain_error(_: Request, exc: Exception) -> JSONResponse:
        code = exc.code  # type: ignore[attr-defined]
        return _error(STATUS_BY_CODE[code], code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return _error(422, "validation_error", f"{where or 'body'}: {first['msg']}")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/games", status_code=201, response_model=GameOut)
    def create_game(response: Response) -> GameOut:
        game_id, game = repo.create()
        response.headers["Location"] = f"/games/{game_id}"
        return game_out(game_id, game)

    @app.get("/games", response_model=list[GameSummary])
    def list_games() -> list[GameSummary]:
        return [GameSummary(id=i, status=g.status, version=g.version) for i, g in repo.list()]

    @app.get("/games/{game_id}", response_model=GameOut, responses=ERROR_RESPONSES)
    def get_game(game_id: str) -> GameOut:
        return game_out(game_id, repo.get(game_id))

    @app.post("/games/{game_id}/moves", response_model=GameOut, responses=ERROR_RESPONSES)
    def make_move(game_id: str, req: MoveRequest) -> GameOut:
        game = repo.update(game_id, lambda g: engine.apply_move(g, req.player, req.row, req.col))
        return game_out(game_id, game)

    @app.get("/games/{game_id}/moves", response_model=list[MoveOut], responses=ERROR_RESPONSES)
    def get_moves(game_id: str) -> list[MoveOut]:
        return [move_out(m) for m in repo.get(game_id).moves]

    # Mounted last so every API route above wins; serves the UI at /.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
