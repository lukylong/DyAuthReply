# Repository Guidelines

## Project Structure & Module Organization

This repository contains two main services:

- **`web/`** — pnpm + Turbo monorepo. Vue 3 + Element Plus frontend.
  - `apps/web-ele` — the single application entry point
  - `packages/` — shared packages (`@core/`, `effects/`, `stores/`, `locales/`, `styles/`, `types/`, `utils/`, etc.)
  - `internal/` — tooling configs (`eslint-config`, `prettier-config`, `stylelint-config`, `tailwind-config`, `vite-config`)
- **`backend-django/`** — Django 5.2 + django-ninja REST API.
  - `application/` — Django project config (settings, urls, asgi, wsgi, celery)
  - `core/` — main Django app with 30+ domain modules (user, auth, douyin, dept, etc.)
  - `common/` — shared infrastructure (base model, CRUD helpers, auth, pagination, schemas)
  - `scheduler/` — APScheduler-based task scheduling

## Build, Test, and Development Commands

### Frontend (run from `web/`)

| Command | Purpose |
|---------|---------|
| `pnpm install` | Install workspace dependencies |
| `pnpm dev` | Start dev server (Vite HMR) |
| `pnpm build` or `pnpm build:ele` | Production build |
| `pnpm lint` | Run ESLint + Stylelint |
| `pnpm format` | Prettier format |
| `pnpm check:type` | Full workspace type-check |
| `pnpm test:unit` | Run all Vitest tests |
| `pnpm vitest run packages/stores/src/modules/user.test.ts` | Run a single test file |
| `pnpm vitest run -t "returns correct userInfo"` | Run tests matching a name pattern |
| `pnpm vitest run --dom packages/effects/request/src/request-client/request-client.test.ts` | Run a single file with dom env |

### Backend Django (run from `backend-django/`)

```bash
# Setup
pip install -r requirements.txt
python manage.py migrate

# Run server
python manage.py runserver 0.0.0.0:8000   # dev (SQLite default)
# ZQ_ENV=prd python manage.py runserver   # production env

# Tests
python manage.py test                                          # all tests
python manage.py test core.tests.test_douyin_rule_api          # single test file
python manage.py test core.tests.test_douyin_rule_api.DouyinRuleApiTests  # single test class
python manage.py test core.tests.test_douyin_rule_api.DouyinRuleApiTests.test_quick_enable_payload_uses_contains_when_keywords_present  # single test method
```

Test runner is Django's built-in `django.test`. Tests live under `core/tests/` and use `unittest`-style `SimpleTestCase` or `IsolatedAsyncioTestCase` (for async). No pytest.

## Coding Style & Formatting

### General
- `.editorconfig` at repo root: UTF-8, LF, 2-space indent, max line **100 chars**, single quotes
- Trailing commas enabled in Prettier; trailing whitespace trimmed (except `.md` files)

### Frontend (TypeScript / Vue)

**ESLint** — flat config via `@vben/eslint-config`. Strict import ordering enforced by `perfectionist/sort-imports`:
1. Type imports first (external type → vue type → `@vben/*` type → `@vben-core/*` type → parent/sibling/index type → internal type)
2. Value imports (builtin → vue → `@vben/*` → `@vben-core/*` → external → internal `#/` → parent/sibling/index → side-effect → style)
3. Groups separated by blank lines; ascending/natural sort within each group

Additional import rules:
- `import/first`, `newline-after-import`, `no-duplicates`, `consistent-type-specifier-style: prefer-top-level`

**Prettier** — `singleQuote: true`, `semi: true`, `printWidth: 80`, `trailingComma: 'all'`. Tailwind class sorting via `prettier-plugin-tailwindcss`.

**Vue component conventions:**
- File: **kebab-case.vue** in its own directory (e.g., `zq-dialog/zq-dialog.vue`)
- `defineOptions({ name: 'PascalCase' })` in every component
- Macro order: `defineOptions → defineProps → defineEmits → defineSlots`
- Template: PascalCase component tags, kebab-case HTML attributes, self-closing void elements
- Multi-word component names are NOT enforced (single-word like `App` is allowed)

**Naming:** API functions use `{action}{Entity}Api` (e.g., `getUserListApi`, `createUserApi`). Interface types are PascalCase with `Input`/`Params`/`Output` suffixes (e.g., `UserCreateInput`, `UserListParams`).

**Path alias:** `#/` → `src/` for internal imports. External shared types via `@vben/types` or `@vben-core/*`.

**API request architecture:** Two clients — `requestClient` auto-unwraps `{code:0,data:...}` responses; `baseRequestClient` returns raw Axios responses. Interceptors handle 401 token refresh (queued), error formatting via i18n keys, and a `codeField`/`dataField` response convention (`code: 0` = success). Stream requests use native `fetch` with `AbortController`.

**State management:** Pinia with `pinia-plugin-persistedstate`. Production uses AES-encrypted `secure-ls`. Store IDs follow `core-{name}` pattern. `useAccessStore` persists tokens; `useUserStore` and `useTimezoneStore` are memory-only.

**Styling:** Tailwind with custom color system via CSS variables (`hsl(var(--primary))`). Component styles use BEM naming. Stylelint enforces property order and class naming pattern.

### Backend (Python / Django)

**Imports order:** standard library → third-party → local. Within local: `common.*` → `core.*`. All absolute.

**Naming conventions:**
- Files: snake_case (`user_model.py`, `user_api.py`, `user_schema.py`)
- Models: PascalCase singular noun, inherit `common.fu_model.RootModel` (provides UUID PK, soft delete, timestamps, creator/modifier audit fields)
- Each domain has 3 files: `{entity}_model.py`, `{entity}_schema.py`, `{entity}_api.py`
- API endpoints: `router = Router()` with snake_case functions (`create_user`, `list_user`, `update_user`)
- Schemas: `{Entity}SchemaIn`, `{Entity}SchemaOut`, `{Entity}SchemaPatch`, `{Entity}Filters`
- Database tables: `core_{entity}` (explicit `db_table` in Meta)

**Router registration:** All domain routers are registered in `core/router.py`. The top-level NinjaAPI in `application/main.py` mounts `core/router.py:core_router` at `/core` and `scheduler/router.py:scheduler_router` at `/scheduler`. New modules must be added to `core/router.py`.

**Error handling:**
- Business logic errors: `raise HttpError(400, "message")` from `ninja.errors`
- Auth errors: `HttpError(401)` / `HttpError(403)` in `common/fu_auth.py`
- Not-found: `get_object_or_404()` from `django.shortcuts`
- Schema validation: Pydantic `@field_validator` in schema classes
- Rate limiting: `LoginAttemptProtection` class (Redis-backed, 15-min lockout after 15 failures in 5 min)
- Token blacklisting: `TokenBlacklist` class invalidates tokens on logout/password change

**Common helpers:** `common/fu_crud.py` provides `create()`, `retrieve()`, `delete()`, `update()`, `batch_create()`, `batch_delete()`. `common/fu_pagination.py` provides `MyPagination` (page/pageSize params). `common/fu_schema.py` provides `FuFilters` base and `response_success()` helper.

**Foreign keys** use `db_constraint=False` — constraints enforced at application level. `select_related()` and `prefetch_related()` must be used for efficient queries.

## Testing Guidelines

### Frontend
- Framework: Vitest with `happy-dom` environment
- Files: `*.test.ts` in `__tests__/` directories or adjacent to source
- Store tests need `createPinia()` + `setActivePinia()` in `beforeEach`
- Request tests use `axios-mock-adapter` for HTTP mocking
- Test helpers from `vitest`: `describe`, `it`, `expect`, `beforeEach`, `vi`

### Backend
- Django's `SimpleTestCase` for non-DB tests, `IsolatedAsyncioTestCase` for async
- No pytest — use `manage.py test` with dot-path notation
- Tests import directly from `core.*` modules (no test database setup needed for unit-level tests)

## Commit & Pull Request Guidelines

- Use **Conventional Commits**: `feat(scope): message`, `fix(scope): message`, `docs: message`, etc.
- PR titles: lowercase subject with semantic type prefix
- PR body: concise description, change type, linked issue (if any), test evidence, screenshots for UI changes
- Keep commit messages brief and descriptive

## Environment Management

- **Backend:** `ZQ_ENV` env var selects environment config (`dev`, `uat`, `prd`). Default is `dev` (SQLite). Config modules in `backend-django/env/` (`dev_env.py`, `uat_env.py`, `prd_env.py`).
- **Frontend:** `VITE_` prefix for all frontend environment variables. Build modes in `web/apps/web-ele/.env*` files.

## Security & Configuration

- Secrets in env files only (`backend-django/env/*` with `dev_env.py`, `uat_env.py`, `prd_env.py`); never commit credentials
- Environment selected via `ZQ_ENV` env var (dev/uat/prd)
- Frontend env vars prefixed with `VITE_`
- Encrypted local storage in production (Pinia persistedstate via `secure-ls` with AES)
- JWT auth via `django-ninja` BearerAuth (HS256, 24h access / 7d refresh)
- Douyin credentials stored encrypted (Fernet via `cryptography` library)
- Rate limiting and token blacklisting via Redis cache
