# Contributing to CarGurus Video Pipeline

## Branch Naming Convention

Every feature branch must follow this pattern:

```
<type>/<short-description>
```

**Types:**
- `feat/` — New feature (e.g., `feat/batch-video-generation`)
- `fix/` — Bug fix (e.g., `fix/sora-polling-timeout`)
- `refactor/` — Code restructuring (e.g., `refactor/split-app-routes`)
- `chore/` — Maintenance, deps, CI (e.g., `chore/update-dependencies`)

**Rules:**
1. Each branch should address **one concern**. If you find yourself adding unrelated fixes, create a separate branch.
2. Branch names should be under 50 characters.
3. Use kebab-case (lowercase, hyphens).
4. Never use a catch-all branch name like `resume-work` or `various-fixes`.

## Project Structure

```
app.py              — Flask app setup, startup, blueprint registration
routes/
  upload.py         — Upload + VIN + media generation endpoints
  vehicles.py       — Vehicle CRUD, branding, costs, overlay, templates
  media.py          — Media library + people photo endpoints
workers/
  pipeline.py       — Unified Pipeline class for video generation
config/
  settings.py       — All configuration (env vars, costs, paths)
scripts/
  multimodal_extractor.py  — Gemini extraction
  vin_script_generator.py  — VIN-based script generation
video_gen/
  sora_generator.py — OpenAI Sora integration
  veo_generator.py  — Google Veo integration (fallback)
  overlay.py        — FFmpeg compositing
utils/
  database.py       — SQLite schema + CRUD
  cost_tracker.py   — Budget enforcement
  cloud_storage.py  — GCS integration
  data_persistence.py — Firestore + JSON backup
  logger.py         — Structured logging
  retry.py          — Exponential backoff
  vin_decoder.py    — NHTSA VIN decoding
tests/
  test_routes.py    — API integration tests
  test_database.py  — Database CRUD tests
  test_cost_tracker.py
  test_retry.py
  test_video.py
```

## Adding a New Feature

1. Create a properly scoped branch: `git checkout -b feat/your-feature`
2. If it touches the web API, add the route to the appropriate blueprint in `routes/`
3. If it touches the generation pipeline, modify `workers/pipeline.py`
4. Add tests in `tests/`
5. Run `python -m pytest tests/ -v` before pushing
6. Keep PRs focused — one feature per PR

## Code Conventions

- Use `utils.logger.get_logger(name)` for all logging
- Never silence exceptions with bare `except Exception: pass` — always log
- All dynamic SQL field names must go through `_validate_field_names()` in `database.py`
- File uploads must be validated via `_validate_upload_file()` in `routes/upload.py`
- Configuration belongs in `config/settings.py`, not hardcoded in business logic
