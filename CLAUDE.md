# Conventions

See `Plan.md` for scope, architecture and milestones. Work one milestone at a time.

- Rules live only in `app/engine.py` (pure, no I/O). API and UI never re-implement rules.
- State = append-only move log; the board is derived by replay.
- Tests first for engine behaviour; engine branch coverage must stay at 100%.
- Commands: `make install`, `make lint`, `make test`, `make run`.
- Commits: conventional messages (`feat(engine): ...`), small, one concern each.
- Secrets: never commit `.env`, `*.db`; only `.env.example` with placeholders. Stage files by path.
- Do not commit/push unless explicitly asked.
