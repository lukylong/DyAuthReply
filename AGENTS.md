# Repository Guidelines

## Project Structure & Module Organization
This repository contains three main parts:

- `web/`: pnpm + Turbo monorepo for the Vue 3 + Element Plus frontend. Main app is `web/apps/web-ele`; shared packages live in `web/packages/*`; internal tooling/configs are in `web/internal/*`.
- `backend-fastapi/`: async FastAPI service (`main.py`) with domain modules in `core/`, shared app code in `app/`, and DB migrations in `alembic/`.
- `backend-django/`: Django service (`manage.py`) with business modules under `core/` and scheduler logic in `scheduler/`.

## Build, Test, and Development Commands
Frontend (run from `web/`):

- `pnpm install`: install workspace dependencies.
- `pnpm dev`: start `@vben/web-ele` in development mode.
- `pnpm build` or `pnpm build:ele`: production build.
- `pnpm lint` and `pnpm format`: lint/format TS, Vue, and styles.
- `pnpm check:type`: workspace type-check.
- `pnpm test:unit`: run Vitest unit tests.

Backends:

- FastAPI: `cd backend-fastapi && pip install -r requirements.txt && alembic upgrade head && python main.py`
- Django: `cd backend-django && pip install -r requirements.txt && python manage.py migrate && python manage.py runserver 0.0.0.0:8000`

## Coding Style & Naming Conventions
- Follow `web/.editorconfig`: UTF-8, LF, 2-space indent, max line length 100.
- Use existing lint presets (`web/eslint.config.mjs`, `web/.prettierrc.mjs`, `web/stylelint.config.mjs`); do not introduce local style overrides.
- Keep naming consistent with current modules: frontend helpers commonly use kebab-case files (for example `generate-routes-frontend.ts`), Python modules use snake_case (for example `login_log`).

## Testing Guidelines
- Frontend uses Vitest (`web/vitest.config.ts`, `happy-dom`).
- Place frontend tests as `*.test.ts` in `__tests__/` or beside the module, matching existing patterns.
- Backend automated coverage is currently limited; for backend changes, add focused tests near the affected module and include run instructions in the PR.

## Commit & Pull Request Guidelines
- Prefer Conventional Commits (see `.github/commit-convention.md`): `feat(scope): ...`, `fix(scope): ...`, `docs: ...`.
- Recent history includes brief messages (for example `fix login`), but new contributions should use the conventional format for consistent changelogs.
- PR titles should follow semantic types (`feat|fix|docs|chore|refactor|perf|test|build|ci|revert`) and use a lowercase subject.
- PRs should include: concise description, change type, linked issue (if any), test evidence, and screenshots for UI changes.

## Security & Configuration Tips
- Keep secrets in local env files (`backend-fastapi/env/*.env`, Django config/env files); never commit credentials or private keys.
- Treat files like `rsa-private-key.pem` as local-only material and rotate immediately if exposed.
