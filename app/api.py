"""FastAPI routes. Translate HTTP <-> engine; all rules stay in app.engine."""

from __future__ import annotations

import os
import random
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app import ai, engine
from app.events import EventBroker, EventStreamResponse, game_event_stream, parse_last_event_id
from app.limits import describe, validate_config, validate_opponent
from app.models import (
    ConfigOut,
    CreateGameRequest,
    ErrorOut,
    GameOut,
    GameSummary,
    MoveOut,
    MoveRequest,
    game_out,
    move_out,
    opponent_out,
)
from app.store import GameNotFound, GameRepository, SqliteRepository

STATIC_DIR = Path(__file__).parent / "static"

STATUS_BY_CODE = {
    GameNotFound.code: 404,
    engine.GameOver.code: 409,
    engine.CellTaken.code: 409,
    engine.NotYourTurn.code: 409,
    engine.StaleVersion.code: 409,
    engine.OutOfBounds.code: 422,
    engine.InvalidConfig.code: 422,
}

ERROR_RESPONSES = {
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
    422: {"model": ErrorOut},
}


class ExpectedVersionRequired(Exception):
    """A move in a game against the computer must say which version it is based on.

    The turn passes back to the human as soon as the computer has replied, so
    without it a retried request would be accepted as one more human move.
    """


def _error(status: int, code: str, message: str, **extra: int) -> JSONResponse:
    detail = {"code": code, "message": message, **extra}
    return JSONResponse(status_code=status, content={"error": detail})


def create_app(
    repo: GameRepository | None = None,
    keepalive_seconds: float | None = None,
    rng: random.Random | None = None,
) -> FastAPI:
    repo = repo or SqliteRepository(os.environ.get("DB_PATH", "tictactoe.db"))
    if keepalive_seconds is None:
        keepalive_seconds = float(os.environ.get("SSE_KEEPALIVE_SECONDS", "5"))
    rng = rng or random.Random()  # tests inject a seeded one for reproducible computer moves
    ai.warm_up()  # solve the game once now, never inside a request's write lock
    broker = EventBroker()
    app = FastAPI(title="Tic-Tac-Toe")
    app.state.broker = broker

    @app.exception_handler(engine.EngineError)
    @app.exception_handler(GameNotFound)
    async def domain_error(_: Request, exc: Exception) -> JSONResponse:
        code = exc.code  # type: ignore[attr-defined]
        stale = isinstance(exc, engine.StaleVersion)
        extra = {"current_version": exc.current_version} if stale else {}
        return _error(STATUS_BY_CODE[code], code, str(exc), **extra)

    @app.exception_handler(ExpectedVersionRequired)
    async def expected_version_required(_: Request, exc: Exception) -> JSONResponse:
        return _error(
            422,
            "validation_error",
            "expected_version: required for a game against the computer",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return _error(422, "validation_error", f"{where or 'body'}: {first['msg']}")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config", response_model=ConfigOut)
    def get_config() -> dict:
        return describe()

    @app.post("/games", status_code=201, response_model=GameOut, responses=ERROR_RESPONSES)
    def create_game(response: Response, req: CreateGameRequest | None = None) -> GameOut:
        req = req or CreateGameRequest()  # no body == all defaults (3x3, k=3)
        validate_config(req.rows, req.cols, req.k)  # before anything is allocated
        validate_opponent(req.rows, req.cols, req.k, req.opponent, req.difficulty, req.human_plays)
        opponent, opening = None, ()
        if req.opponent == "computer":
            human = req.human_plays or "X"
            opponent = ai.Opponent(ai.other(human), req.difficulty or "medium")
            opening = ai.open_game(opponent, rng).moves  # the computer opens when it is X
        game_id, game = repo.create(req.rows, req.cols, req.k, opponent, opening)
        response.headers["Location"] = f"/games/{game_id}"
        return game_out(game_id, game, opponent)

    @app.get("/games", response_model=list[GameSummary])
    def list_games() -> list[GameSummary]:
        return [
            GameSummary(
                id=i,
                rows=g.rows,
                cols=g.cols,
                k=g.k,
                status=g.status,
                version=g.version,
                opponent=opponent_out(repo.opponent_of(i)),
            )
            for i, g in repo.list()
        ]

    @app.get("/games/{game_id}", response_model=GameOut, responses=ERROR_RESPONSES)
    def get_game(game_id: str) -> GameOut:
        return game_out(game_id, repo.get(game_id), repo.opponent_of(game_id))

    @app.post("/games/{game_id}/moves", response_model=GameOut, responses=ERROR_RESPONSES)
    def make_move(game_id: str, req: MoveRequest) -> GameOut:
        opponent = repo.opponent_of(game_id)  # fixed for the game's life, so safe to read first
        if opponent is None:
            game = repo.update(
                game_id,
                lambda g: engine.apply_move(g, req.player, req.row, req.col, req.expected_version),
            )
        else:
            if req.expected_version is None:
                raise ExpectedVersionRequired
            # The human move and the computer's reply are one atomic update.
            game = repo.update(
                game_id,
                lambda g: ai.play_turn(
                    g, opponent, req.player, req.row, req.col, req.expected_version, rng
                ),
            )
        broker.publish(game_id)  # only after the turn is saved
        return game_out(game_id, game, opponent)

    @app.get("/games/{game_id}/moves", response_model=list[MoveOut], responses=ERROR_RESPONSES)
    def get_moves(game_id: str) -> list[MoveOut]:
        return [move_out(m) for m in repo.get(game_id).moves]

    @app.get("/games/{game_id}/events", responses={404: {"model": ErrorOut}})
    async def game_events(game_id: str, request: Request) -> EventStreamResponse:
        await run_in_threadpool(repo.get, game_id)  # unknown id: JSON 404 before streaming
        last_event_id = parse_last_event_id(request.headers.get("last-event-id"))
        return EventStreamResponse(
            game_event_stream(repo, broker, game_id, last_event_id, keepalive_seconds),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Mounted last so every API route above wins; serves the UI at /.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
