# Diagnostic Booking Service

A backend service for booking diagnostic tests (blood tests, scans, …) at diagnostic centres
and paying for them through a **simulated** payment provider, with **signed, idempotent
payment webhooks**.

- **Stack**: FastAPI · PostgreSQL · SQLAlchemy 2 · Alembic · Pydantic v2 · JWT · Docker
- **Features**:
  - JWT authentication with user and admin roles.
  - Centres and tests, with **per-centre pricing** stored in a many-to-many association.
  - Bookings priced **server-side**, with an explicit state machine.
  - Simulated payments, with payment history and **reconciliation of stuck payments**.
  - Webhooks that are safe against retries and **concurrent duplicates**, with an admin-visible
    event log.
  - **Unpaid bookings expire** after a hold window, releasing their slot.
  - Patients can **reschedule or cancel (with refund)** confirmed bookings up to a cutoff; admins
    record **attendance** (completed / no-show).
  - **Preparation details** per test (sample type, fasting, instructions, report turnaround).
  - Admin **reporting**, **user management** and a background **maintenance job**.
- **Extras**: a browser UI served at `/ui/`, and Swagger at `/docs`.
- **Quality**: 277 automated tests run against real PostgreSQL, including concurrency tests.

---

## Contents

1. [Quick start](#1-quick-start)
2. [Running the project locally](#2-running-the-project-locally)
3. [Web UI](#3-web-ui)
4. [Technology stack](#4-technology-stack)
5. [Project structure & architecture](#5-project-structure--architecture)
6. [Database / schema design](#6-database--schema-design)
7. [Environment variables](#7-environment-variables)
8. [API endpoints](#8-api-endpoints)
9. [Example requests](#9-example-requests)
10. [Booking lifecycle](#10-booking-lifecycle)
11. [Payments](#11-payments)
12. [Webhook idempotency](#12-webhook-idempotency)
13. [Authorization & security](#13-authorization--security)
14. [Edge cases handled](#14-edge-cases-handled)
15. [Assumptions](#15-assumptions)
16. [Testing](#16-testing)
17. [What I would improve with more time](#17-what-i-would-improve-with-more-time)

---

**Where to find what**: run locally → [1](#1-quick-start), [2](#2-running-the-project-locally) ·
API endpoints → [8](#8-api-endpoints) · example requests → [9](#9-example-requests) ·
database/schema → [6](#6-database--schema-design) · assumptions → [15](#15-assumptions) ·
improvements → [17](#17-what-i-would-improve-with-more-time) · tests → [16](#16-testing).

---

## 1. Quick start

Requires **Docker Desktop** (or Docker Engine with Compose v2) and **git**.

```bash
git clone <repository-url> diagnostic-booking-service
cd diagnostic-booking-service
cp .env.example .env                 # then put real random values in JWT_SECRET_KEY and WEBHOOK_SECRET
docker compose up --build -d         # starts PostgreSQL, the API and the maintenance job; migrations run automatically

# load demo data (6 centres, 8 tests, different prices per centre) plus example logins
docker compose exec api python -m app.scripts.seed_demo --with-accounts
```

`--with-accounts` creates these example logins (skipped if the email already exists):

| Role | Email | Password |
|---|---|---|
| Admin | `admin@example.com` | `DemoAdmin#2026` |
| Patient | `patient@example.com` | `DemoPatient#2026` |

These passwords are public, so use them only on a local or demo database. For a real admin,
create one with your own password instead (admins cannot self-register):

```bash
docker compose exec -e ADMIN_PASSWORD='choose-a-strong-password' api python -m app.scripts.create_admin --email you@yourdomain.com --name "Admin"
```

Then open:

| URL | What |
|---|---|
| <http://localhost:8000/> | Web UI. Sign in with an example login above, or create a patient account. |
| <http://localhost:8000/docs> | Swagger UI (interactive API docs) |
| <http://localhost:8000/redoc> | ReDoc |
| <http://localhost:8000/health> | Health check. Returns `{"status":"ok"}` |

---

## 2. Running the project locally

### Prerequisites

| Tool | Needed for | Version |
|---|---|---|
| Docker + Docker Compose v2 | Option A (everything), and the database for Option B | Docker Desktop 4.x / Engine 24+ |
| Python | Option B and running the tests | **3.12 or newer** |
| git | Cloning | any |

### Step 0: create `.env` (both options)

The app reads its configuration from environment variables or a `.env` file. **Docker Compose
refuses to start without `.env`.**

```bash
cp .env.example .env                 # PowerShell: Copy-Item .env.example .env
```

Replace `JWT_SECRET_KEY` and `WEBHOOK_SECRET` with random values. Each must be **at least 32
characters**, or the app refuses to start. To generate one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

All variables are described in [Environment variables](#7-environment-variables).

### Option A: Docker Compose (recommended)

```bash
docker compose up --build -d         # build and start db, api and maintenance in the background
docker compose logs -f api           # follow API logs (Ctrl+C stops following, not the app)
```

- The `api` container waits for PostgreSQL to be healthy, runs `alembic upgrade head`, then
  starts Uvicorn on port 8000.
- The `maintenance` container runs `python -m app.scripts.maintenance --every 60`: it expires
  unpaid bookings past their hold and settles payments stuck in `PENDING`.
- Load demo data and the example logins as shown in the [Quick start](#1-quick-start).

### Option B: run the API with Python (database in Docker)

This is useful for development with auto-reload.

```bash
python -m venv .venv
source .venv/bin/activate            # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

docker compose up -d db              # only PostgreSQL

# .env points at host "db" (the Docker network name). From your machine the database is on
# localhost, so override DATABASE_URL. Environment variables take precedence over .env.
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking
# PowerShell: $env:DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking"

alembic upgrade head                 # create the schema
python -m app.scripts.seed_demo --with-accounts   # demo data + example admin/patient logins
uvicorn app.main:app --reload        # http://localhost:8000
```

Optionally, in a second terminal (same virtual environment and `DATABASE_URL`), run the
maintenance job that Compose would otherwise run for you:

```bash
python -m app.scripts.maintenance --every 60
```

> **Using your own PostgreSQL** (14+) instead of Docker: create an empty database (for example
> `createdb diagnostic_booking`) and point `DATABASE_URL` at it.

### Switching the payment simulation mode

Payments are simulated. `PAYMENT_SIMULATION_MODE` in `.env` chooses the outcome: `success`
(default), `failure`, `random`, or `pending`. Use `pending` to exercise the webhook flow.

- **Docker**: edit `.env`, then recreate the containers with `docker compose up -d api maintenance`.
  Containers read `.env` only when they are created, so setting the variable in your shell has
  no effect.
- **Option B**: set it in `.env` or in your shell, then restart Uvicorn.

### Stopping and resetting

```bash
docker compose down                  # stop containers; the database is kept in a Docker volume
docker compose down -v               # stop AND delete all data (fresh database on next start)
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `env file .env not found` or `Set POSTGRES_PASSWORD in .env` | You skipped Step 0: `cp .env.example .env`. |
| API exits with `jwt_secret_key … must be at least 32 characters long` | Use a longer secret (see Step 0). |
| `port is already allocated` (5432 or 8000) | Set `POSTGRES_PORT=5433` or `API_PORT=8001` in `.env`. If you change `POSTGRES_PORT`, use the new port in your local `DATABASE_URL`. |
| `password authentication failed` after changing `POSTGRES_PASSWORD` | The volume keeps the original password. Run `docker compose down -v` (deletes data), or change `DATABASE_URL` back. |
| `failed to connect to the docker API` | Start Docker Desktop. |
| Example admin login says "Incorrect email or password" | `admin@example.com` already existed with another password (the seed never overwrites existing users). Use that password, or run `docker compose down -v` and seed again. |
| Tests fail with `database "diagnostic_booking_test" does not exist` | Create it (see [Testing](#16-testing)). |

---

## 3. Web UI

<http://localhost:8000/> redirects to `/ui/`. The UI is a single-page app in `frontend/`,
written in plain HTML, CSS and JavaScript ES modules with **no build step**. FastAPI serves it as
static files from the same origin as the API, so it needs no CORS configuration and no Node
toolchain.

The layout is a calm, clinic-style dashboard: a sidebar with role-specific navigation (a compact
top bar on phones), large headings with small uppercase "eyebrow" labels, soft sage surfaces with
a deep evergreen for actions and a warm honey accent, and Manrope/Inter typography.

**Patients**

| Area | What you can do |
|---|---|
| **Sign in / Create account** | Sign up and sign in. The JWT is kept in `localStorage` and sent as a Bearer token. |
| **Overview** | A greeting, a reminder for an unpaid booking or an appointment in the next 48 hours (with fasting/preparation advice), quick stats, and the next appointments. |
| **Find a test** | Search the catalogue. Cards show sample-type, fasting and report-time chips. *Compare centres* lists every centre offering the test, cheapest first, with a *Best price* tag and the price difference for the others. |
| **Centres** | Filter centres by name and location, and see each centre's tests and prices. |
| **Booking** | Pick a date, then a time from a grid of suggested slots. Times already booked (from `GET …/booked-times`) or in the past are crossed out, and any exact time can still be chosen. The dialog shows the test's preparation notes and price. After booking, *Pay now* or *Pay later*; the hold time is shown. |
| **My bookings** | Date tiles, status badges, filters and pagination. *Pay* (with a "pay within" countdown), *Reschedule*, *Cancel* (confirmed bookings are refunded; the free-change deadline is shown), *Book again* for failed/expired/cancelled bookings, and *Details* with payment history and preparation notes. |

**Admins**

| Area | What you can do |
|---|---|
| **Overview** | Bookings, revenue, refunds, payment success rate and upcoming visits for 7/30/90 days; a bookings-by-status bar, revenue by centre, most-booked tests, and a *Run maintenance* button. |
| **Bookings** | Every patient's bookings: reschedule, cancel & refund, mark *Attended* / *Missed* after the appointment, and *Reconcile* a stuck payment from the booking details. |
| **Catalogue** | Centres, tests (including sample type, fasting hours, report turnaround and patient preparation) and centre-specific pricing. |
| **Users** | Search users, see their booking counts, make/remove admins, deactivate/reactivate accounts. |
| **Payment webhooks** | The webhook ledger: filter by outcome (applied / no change / rejected), search, and inspect each raw payload. |

- **Errors**: messages come straight from the API (for example *"This time slot is already
  booked…"*).
- **Theme and layout**: light and dark themes (system setting plus a toggle), and a layout that
  works on phones.
- **Security**:
  - API data is always inserted as text, never as HTML, so it can't inject markup.
  - A strict Content-Security-Policy allows scripts only from the same origin. The only
    third-party allowance is Google Fonts (stylesheet and font files). To keep everything
    self-hosted, remove the font `<link>`s in `index.html` and their CSP entries; the UI falls
    back to system fonts.

---

## 4. Technology stack

| Technology | Why |
|---|---|
| **Python 3.12+** | Modern typing (`StrEnum`, PEP 695 generics, `X \| Y` unions). |
| **FastAPI** | Typed request/response models, dependency injection, automatic OpenAPI docs. |
| **PostgreSQL 16** | Transactions, row locks (`SELECT … FOR UPDATE`), partial unique indexes and `INSERT … ON CONFLICT`. The idempotency and anti-double-booking guarantees rely on these. |
| **SQLAlchemy 2.x** (sync, psycopg 3) | Typed ORM models and explicit transactions. Sync code keeps session and transaction handling simple; FastAPI runs sync endpoints in a thread pool. |
| **Alembic** | Versioned schema migrations. |
| **Pydantic v2 / pydantic-settings** | Input validation, response schemas, and config from the environment. |
| **PyJWT** | JWT access tokens (HS256). |
| **bcrypt** | Password hashing. Used directly, because `passlib` is unmaintained. |
| **pytest + httpx** | API tests through FastAPI's `TestClient`, plus multithreaded concurrency tests against the real database. |
| **Docker / Compose** | One command starts the API and PostgreSQL. |
| **ruff** | Linting and formatting (dev only). |

**Not used: Redis and Celery.** Nothing in the requirements needs them. PostgreSQL provides the
locking and uniqueness guarantees, and the simulated provider responds immediately.

---

## 5. Project structure & architecture

```
diagnostic-booking-service/
├── app/
│   ├── main.py              App factory: routers, error handlers, OpenAPI tags, /ui mount
│   ├── core/
│   │   ├── config.py        Typed settings from environment / .env
│   │   ├── security.py      bcrypt hashing, JWT encode/decode, webhook HMAC signatures
│   │   └── exceptions.py    AppError hierarchy + handlers → consistent {"detail": ...} errors
│   ├── db/
│   │   ├── base.py          Declarative Base, constraint naming convention, timestamps, MONEY type
│   │   ├── database.py      Engine, session factory, request-scoped get_db dependency
│   │   └── utils.py         Pagination, LIKE escaping, constraint-violation helpers
│   ├── models/              SQLAlchemy models, incl. booking/payment state machines
│   │   └── user.py, centre.py (+ centre_tests), test.py, booking.py, payment.py, webhook.py
│   ├── schemas/             Pydantic request/response models (one module per resource)
│   ├── api/                 Thin routers: validate input → call one service → return result
│   │   └── auth.py, centres.py, tests.py, bookings.py, payments.py, webhooks.py, admin.py, common.py
│   ├── services/            Business logic + transaction boundaries
│   │   ├── auth_service.py
│   │   ├── centre_service.py            centres + centre–test offerings
│   │   ├── diagnostic_test_service.py
│   │   ├── booking_service.py           bookings, hold expiry, reschedule, attendance, booked times
│   │   ├── payment_service.py           payments, history, reconciliation, maintenance run
│   │   ├── admin_service.py             reporting, user management, webhook ledger
│   │   ├── payment_provider.py          simulated provider (success/failure/random/pending)
│   │   └── webhook_service.py           idempotent webhook processing
│   ├── dependencies/        current user, admin guard, payment provider, webhook signature check
│   └── scripts/
│       ├── create_admin.py  Create an admin (or promote an existing user)
│       ├── seed_demo.py     Load demo centres/tests/prices/preparation details (idempotent)
│       └── maintenance.py   Expire lapsed holds + settle stuck payments (once, or --every N s)
├── frontend/                Browser UI (ES modules, no build step)
│   ├── index.html, styles.css, theme.js
│   ├── app.js               Shell, routing, sign-in, patient pages
│   ├── booking.js           Booking/payment flows, time picker, booking rows and details
│   ├── admin.js             Admin overview, bookings, catalogue, users, webhook log
│   └── store.js, ui.js, api.js   Shared state, components, API client
├── alembic/                 Migration environment + versions/0001…0003
├── tests/                   pytest suite (conftest.py fixtures, helpers.py, test_*.py)
├── Dockerfile               API image (python:3.12-slim, non-root user)
├── docker-entrypoint.sh     Runs `alembic upgrade head`, then the server
├── docker-compose.yml       db (postgres:16) + api + maintenance
├── alembic.ini
├── pyproject.toml           pytest + ruff configuration
├── requirements.txt         Runtime + test dependencies
├── requirements-dev.txt     + ruff
└── .env.example             Configuration template (copy to .env)
```

**How a request flows:**

- **Routers** (`app/api`) contain no business logic. They declare input/output schemas and auth
  dependencies, then call one service function.
- **Services** (`app/services`) implement the use cases and own the transaction: each use case
  ends in exactly one `commit()`. Any error leaves the transaction uncommitted, and the
  request-scoped session rolls it back.
- **State machines** live on the models (`Booking.transition_to`, `Payment.transition_to`).
  Every status change is checked against an explicit allowed-transitions table.
- **Errors**:
  - Services raise typed errors (`NotFoundError`, `ConflictError`, …), and one handler turns
    them into `{"detail": "..."}`.
  - Unexpected exceptions become a generic 500. Stack traces and database errors are logged,
    never returned to the client.

---

## 6. Database / schema design

PostgreSQL, managed by Alembic (`alembic/versions/`: `0001` initial schema, `0002` refunds,
`0003` extra booking states and test preparation details).

### Entity–relationship diagram

```
┌──────────────┐         ┌────────────────────┐         ┌───────────────────┐
│ users        │         │ diagnostic_centres │         │ diagnostic_tests  │
├──────────────┤         ├────────────────────┤         ├───────────────────┤
│ id PK        │         │ id PK              │         │ id PK             │
│ name         │         │ name               │         │ name UNIQUE       │
│ email UNIQUE │         │ location (indexed) │         │ description NULL  │
│ password_hash│         │ UNIQUE(name,       │         │ base_price NUMERIC│
│ role         │         │        location)   │         └─────────┬─────────┘
│ is_active    │         └─────────┬──────────┘                   │
└──────┬───────┘                   │ 1                           1 │
       │                           │        ┌──────────────────┐   │
       │                           └──────N─┤ centre_tests     ├─N─┘
       │                                    ├──────────────────┤
       │                                    │ id PK            │
       │                                    │ centre_id FK     │  ON DELETE CASCADE
       │                                    │ test_id FK       │  ON DELETE CASCADE
       │                                    │ price NUMERIC >0 │  ← centre-specific price
       │                                    │ UNIQUE(centre_id,│
       │                                    │        test_id)  │
       │                                    └──────────────────┘
       │ 1
       │ N   ┌──────────────────────────────────────┐
       └─────┤ bookings                             │
             ├──────────────────────────────────────┤
             │ id PK                                │
             │ user_id   FK → users      RESTRICT   │
             │ test_id   FK → tests      RESTRICT   │
             │ centre_id FK → centres    RESTRICT   │
             │ appointment_datetime TIMESTAMPTZ     │
             │ amount NUMERIC(10,2) > 0  (snapshot) │
             │ status PENDING|CONFIRMED|FAILED|     │
             │   CANCELLED|EXPIRED|COMPLETED|NO_SHOW│
             │ UNIQUE(centre_id, test_id,           │
             │   appointment_datetime)              │
             │   WHERE status IN (PENDING,CONFIRMED)│
             └──────────────┬───────────────────────┘
                            │ 1
                            │ N  ┌──────────────────────────────────┐
                            └────┤ payments                         │
                                 ├──────────────────────────────────┤
                                 │ id PK                            │
                                 │ booking_id FK → bookings         │
                                 │ provider                         │
                                 │ provider_payment_id UNIQUE       │
                                 │ amount NUMERIC(10,2) > 0         │
                                 │ status PENDING|SUCCESS|FAILED|   │
                                 │        REFUNDED                  │
                                 │ UNIQUE(booking_id)               │
                                 │   WHERE status IN (PENDING,      │
                                 │                    SUCCESS)      │
                                 └──────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ webhook_events   (ledger of every signed webhook event) │
├─────────────────────────────────────────────────────────┤
│ id PK                                                   │
│ event_id UNIQUE          ← the idempotency key          │
│ event_type               payment.success|payment.failed │
│ provider_payment_id, booking_id, reported_status,       │
│ reported_amount, payload JSONB  (raw provider data)     │
│ processed BOOL, outcome APPLIED|NO_OP|REJECTED,         │
│ outcome_detail, received_at, processed_at               │
└─────────────────────────────────────────────────────────┘
(all tables except webhook_events also have created_at / updated_at)
```

### Tables

| Table | Purpose and key constraints |
|---|---|
| `users` | Accounts. Emails are lower-cased before storage, so `UNIQUE(email)` is effectively case-insensitive. `role` is `USER` or `ADMIN`. Only the bcrypt hash of the password is stored. |
| `diagnostic_centres` | Centres. `UNIQUE(name, location)`; indexed on `location` for filtering. |
| `diagnostic_tests` | Test catalogue: one row per test, `UNIQUE(name)`, `base_price` (list price), and optional patient guidance: `sample_type` (BLOOD, URINE, STOOL, SWAB, IMAGING, OTHER), `fasting_hours` (0–72), `preparation_instructions`, `report_turnaround_hours` (1–720). |
| `centre_tests` | **Many-to-many association** between centres and tests that carries the **centre-specific price**. For example, CBC costs ₹500 at Centre A and ₹650 at Centre B without duplicating the test. `UNIQUE(centre_id, test_id)`; rows cascade-delete with their centre or test. |
| `bookings` | A user's appointment for a test at a centre. `amount` is a **snapshot** of the centre price at booking time, so later price changes don't alter it. FKs are `RESTRICT`: centres, tests and users with bookings can't be deleted. A **partial unique index** prevents two active bookings for the same slot. |
| `payments` | Payment attempts. `UNIQUE(provider_payment_id)`. A **partial unique index** allows at most one `PENDING`/`SUCCESS` payment per booking, so a booking can never be charged twice. |
| `webhook_events` | One row per signed webhook event, keyed by the **unique `event_id`**. This is both the idempotency mechanism and an audit log; rejected events are recorded too. `booking_id` is deliberately *not* a foreign key, because a rejected event may reference a booking that doesn't exist. |

### Indexes

| Index | Why |
|---|---|
| `uq_bookings_active_slot` (partial unique: centre_id, test_id, appointment_datetime WHERE status IN PENDING/CONFIRMED) | Race-safe double-booking prevention. Cancelled, failed, expired, completed and no-show bookings free the slot. |
| `uq_payments_active_booking` (partial unique: booking_id WHERE status IN PENDING/SUCCESS) | At most one in-flight or successful payment per booking. |
| `uq_webhook_events_event_id` (unique) | Webhook idempotency. |
| `ix_bookings_user_id_created_at` | "My bookings" listing, newest first. |
| `ix_bookings_status` | Status filter. |
| `ix_centre_tests_test_id` | "Which centres offer test X?" (centre_id is already covered by the unique constraint). |
| `ix_diagnostic_centres_location` | Location filter. |
| `ix_payments_booking_id`, `ix_webhook_events_provider_payment_id` | Payment lookups. |

### Conventions

- **Money** is `NUMERIC(10, 2)` (`Decimal` in Python), never floating point. The API returns
  amounts as strings (`"750.00"`) so clients never lose precision.
- **Timestamps** are `TIMESTAMPTZ`, stored in UTC. `created_at` and `updated_at` are set by the
  database: `now()`, with `updated_at` refreshed on every UPDATE.
- **Integrity**: NOT NULL is used wherever a value is required, and `CHECK` constraints enforce
  positive prices and amounts. Status enums are stored as `VARCHAR` with a `CHECK` constraint,
  which is easier to migrate than native PostgreSQL enums.
- **Constraint names** are deterministic (set by a naming convention), so services can tell
  which constraint was violated and return the right error.

### Migrations

```bash
alembic upgrade head                              # apply all migrations
alembic downgrade -1                              # roll back one migration
alembic revision --autogenerate -m "describe it"  # after changing models
alembic check                                     # verify models and migrations agree
```

Alembic reads `DATABASE_URL` from the environment or `.env`. The Docker image runs
`alembic upgrade head` automatically on start. The test suite builds its schema by running the
same migrations.

---

## 7. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | **yes** | – | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/db`. Use host `db` inside Compose and `localhost` from your machine. |
| `JWT_SECRET_KEY` | **yes** | – | HMAC key for signing JWTs. **At least 32 characters**; the app refuses to start otherwise. |
| `JWT_ALGORITHM` | no | `HS256` | `HS256`, `HS384` or `HS512`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | `60` | Access-token lifetime. |
| `WEBHOOK_SECRET` | **yes** | – | Shared secret for webhook HMAC-SHA256 signatures. At least 32 characters. |
| `PAYMENT_SIMULATION_MODE` | no | `success` | `success`, `failure`, `random` or `pending` (see [Payments](#11-payments)). |
| `PAYMENT_SUCCESS_RATE` | no | `0.8` | Success probability in `random` mode (0–1). |
| `BOOKING_HOLD_MINUTES` | no | `30` | How long an unpaid `PENDING` booking holds its slot before it becomes `EXPIRED`. `0` disables expiry. |
| `PAYMENT_PENDING_TIMEOUT_MINUTES` | no | `15` | After this long, a payment still `PENDING` at the provider is reconciled and, if unresolved, marked `FAILED`. |
| `CANCELLATION_WINDOW_HOURS` | no | `24` | Patients can cancel (with refund) or reschedule a `CONFIRMED` booking until this many hours before it. `0` means until the appointment starts. |
| `LOG_LEVEL` | no | `INFO` | Python log level. |
| `DB_ECHO` | no | `false` | Log every SQL statement (debugging). |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Compose | `postgres` / – / `diagnostic_booking` | Initialise the PostgreSQL container. Must match `DATABASE_URL`. |
| `POSTGRES_PORT` / `API_PORT` | no | `5432` / `8000` | Host ports published by Compose. |
| `TEST_DATABASE_URL` | tests | `postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking_test` | Database used by the test suite. **All its tables are emptied after every test.** |

`.env` is git-ignored. Only `.env.example`, with placeholder values, is committed.

---

## 8. API endpoints

Interactive docs: **Swagger** at `/docs`, **ReDoc** at `/redoc`, and the raw schema at
`/openapi.json`. Endpoints are grouped by tag: Authentication, Centres, Tests, Bookings,
Payments, Webhooks, Admin.

To call protected endpoints from Swagger:

1. Call `POST /auth/login`.
2. Click **Authorize**.
3. Paste the `access_token`.

**Auth column:**

- **Public**: no token needed.
- **User**: any valid JWT (`Authorization: Bearer <token>`).
- **Admin**: the JWT of an admin user.
- **Signed**: an HMAC signature in the `X-Webhook-Signature` header.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/auth/signup` | Public | Register a user (always role `USER`). **201** |
| POST | `/auth/login` | Public | Exchange email and password for a JWT. |
| GET | `/auth/me` | User | Current user's profile. |
| POST | `/centres/` | Admin | Create a centre. **201** |
| GET | `/centres/` | Public | List centres. Filters: `location`, `search` (name). Paginated. |
| GET | `/centres/{centre_id}` | Public | Get a centre. |
| PATCH | `/centres/{centre_id}` | Admin | Update name and/or location. |
| DELETE | `/centres/{centre_id}` | Admin | Delete a centre (409 if it has bookings). **204** |
| GET | `/centres/{centre_id}/tests` | Public | Tests offered by the centre, with its prices. Paginated. |
| GET | `/centres/{centre_id}/tests/{test_id}/booked-times` | Public | Times already held by active bookings in `[start, end)` (at most 31 days). No patient details. |
| POST | `/centres/{centre_id}/tests` | Admin | Offer a test at the centre, optionally at a centre-specific price. **201** |
| PATCH | `/centres/{centre_id}/tests/{test_id}` | Admin | Change the centre's price for a test. |
| DELETE | `/centres/{centre_id}/tests/{test_id}` | Admin | Stop offering the test at the centre. **204** |
| POST | `/tests/` | Admin | Create a diagnostic test. **201** |
| GET | `/tests/` | Public | List tests. Filter: `search` (name or description). Paginated. |
| GET | `/tests/{test_id}` | Public | Get a test. |
| GET | `/tests/{test_id}/centres` | Public | Centres offering the test, **cheapest first**. Paginated. |
| PATCH | `/tests/{test_id}` | Admin | Update a test. |
| DELETE | `/tests/{test_id}` | Admin | Delete a test (409 if it has bookings). **204** |
| POST | `/bookings/` | User | Book a test at a centre. The amount is computed server-side. **201** |
| GET | `/bookings/` | User | Your bookings (admins: all bookings). Filter: `status`. Paginated. |
| GET | `/bookings/{booking_id}` | User | Get one of your bookings (admins: any booking). |
| PATCH | `/bookings/{booking_id}/cancel` | User | Cancel a `PENDING` booking, or a `CONFIRMED` one with a full refund. Patients can cancel confirmed bookings until `CANCELLATION_WINDOW_HOURS` before the appointment (403 after that); admins any time. |
| PATCH | `/bookings/{booking_id}/reschedule` | User | Move a `PENDING` or `CONFIRMED` booking to another time (same centre and test, same price and payment). Same cutoff rule as cancelling. |
| PATCH | `/bookings/{booking_id}/attendance` | Admin | After the appointment time, mark a `CONFIRMED` booking `COMPLETED` or `NO_SHOW`. |
| GET | `/bookings/{booking_id}/payments` | User | Every payment attempt for the booking (owner or admin), including refunds. |
| POST | `/payments/` | User | Pay for your `PENDING` booking via the simulated provider. **201** |
| POST | `/payments/webhook/` | Signed | Payment-provider event callback (idempotent). |
| GET | `/admin/stats?days=30` | Admin | Bookings by status, revenue, refunds, payment success rate, upcoming visits, revenue per centre, top tests. |
| GET | `/admin/users` | Admin | Users with booking counts. Filters: `search` (name/email), `role`. Paginated. |
| PATCH | `/admin/users/{user_id}` | Admin | Set `is_active` and/or `role`. An admin can't demote or deactivate themselves (409). |
| GET | `/admin/webhook-events` | Admin | The webhook ledger with outcomes and raw payloads. Filters: `outcome`, `search`. Paginated. |
| POST | `/admin/payments/{payment_id}/reconcile` | Admin | Settle a payment stuck in `PENDING` (see [Payments](#11-payments)). |
| POST | `/admin/maintenance/run` | Admin | Expire lapsed unpaid bookings and settle timed-out payments now. |
| GET | `/health` | Public | Liveness probe. |
| GET | `/` → `/ui/` | Public | Browser UI (static files; not part of the OpenAPI schema). |

### Request bodies

| Endpoint | Fields (✱ = required) and validation |
|---|---|
| `POST /auth/signup` | `name`✱ (1–100 chars) · `email`✱ (valid email; stored lower-cased) · `password`✱ (8+ chars, max 72 bytes, not a common password) |
| `POST /auth/login` | `email`✱ · `password`✱ → `{"access_token", "token_type": "bearer", "expires_in"}` |
| `POST /centres/` · `PATCH /centres/{id}` | `name`✱ (1–200) · `location`✱ (1–200). PATCH: any subset; `null` is rejected. |
| `POST /tests/` · `PATCH /tests/{id}` | `name`✱ (1–200, unique) · `description` (≤2000) · `base_price`✱ (> 0, ≤ 2 decimals) · `sample_type` · `fasting_hours` (0–72) · `preparation_instructions` (≤2000) · `report_turnaround_hours` (1–720). PATCH: `null` clears the four optional guidance fields and is rejected for the others. |
| `POST /centres/{id}/tests` | `test_id`✱ · `price` (> 0, ≤ 2 decimals; defaults to the test's `base_price`) |
| `PATCH /centres/{id}/tests/{test_id}` | `price`✱ |
| `POST /bookings/` | `test_id`✱ · `centre_id`✱ · `appointment_datetime`✱ (ISO-8601 string; no timezone means UTC; must be in the future and ≤ 365 days ahead). The owner, amount and status are set by the server; any values the client sends for them are ignored. |
| `PATCH /bookings/{id}/reschedule` | `appointment_datetime`✱ (same rules as booking). |
| `PATCH /bookings/{id}/attendance` | `status`✱ (`COMPLETED` or `NO_SHOW`). |
| `PATCH /admin/users/{id}` | `is_active` · `role` (`USER`/`ADMIN`). |
| `POST /payments/` | `booking_id`✱. There is no amount field; the booking's amount is always charged. |
| `POST /payments/webhook/` | `event_id`✱ (1–255, `[A-Za-z0-9_-.:]`) · `payment_id`✱ (the `provider_payment_id`) · `booking_id`✱ · `status`✱ (`SUCCESS`/`FAILED`) · `amount`✱. Header `X-Webhook-Signature`✱. |

### Pagination

List endpoints take `?page=1&page_size=20`. `page` must be ≥ 1, and `page_size` is 1–100
(default 20).

```json
{"items": [...], "total": 42, "page": 1, "page_size": 20, "pages": 3}
```

### Errors

Every error has the shape `{"detail": "..."}`. For 422 validation errors, `detail` is a list of
`{loc, msg, type}`. Submitted values are *not* echoed back, so passwords never appear in error
responses.

| Status | When |
|---|---|
| 400 | Business-rule violation: past appointment, test not offered by the centre, webhook amount mismatch |
| 401 | Missing, invalid or expired JWT; wrong login; invalid webhook signature |
| 403 | Authenticated but not an admin; or a patient changing a confirmed booking inside the cancellation window |
| 404 | Resource doesn't exist, **or belongs to another user** |
| 405 | Method not allowed (for example, there is no endpoint that sets a booking's status directly) |
| 409 | Duplicate (email, centre, test, offering, slot), invalid state transition, booking not payable (including an expired hold), payment not reconcilable yet |
| 422 | Malformed input (types, formats, ranges) |
| 500 | Unexpected error. The response has a generic message only; details are logged. |

---

## 9. Example requests

A complete walkthrough with `curl` (bash, zsh or Git Bash). Each step **captures IDs from the
previous response**, so the steps work whether or not demo data is loaded. Run them in order in
one shell. They assume the example admin from the [Quick start](#1-quick-start) exists
(`seed_demo --with-accounts`), and they use `python` (for JSON parsing and signing) and a fresh
email address.

```bash
API=http://localhost:8000
get() { python -c "import sys, json; print(json.load(sys.stdin)$1)"; }   # tiny JSON field extractor
future() { python -c "import datetime as d; print((d.datetime.now(d.UTC)+d.timedelta(days=$1)).strftime('%Y-%m-%dT$2:00Z'))"; }
EMAIL="john.$(date +%s)@example.com"                                     # unique per run
```

**1. Sign up**

```bash
curl -s -X POST $API/auth/signup -H 'Content-Type: application/json' \
  -d "{\"name\": \"John Doe\", \"email\": \"$EMAIL\", \"password\": \"securepassword\"}"
# 201 {"id":3,"name":"John Doe","email":"john.1790...@example.com","role":"USER","created_at":"..."}
```

**2. Log in** (as the new user, and as the example admin)

```bash
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d "{\"email\": \"$EMAIL\", \"password\": \"securepassword\"}" | get "['access_token']")
ADMIN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "DemoAdmin#2026"}' | get "['access_token']")
# login response: {"access_token":"eyJ...","token_type":"bearer","expires_in":3600}
```

**3. Create a centre** (admin)

```bash
CENTRE_ID=$(curl -s -X POST $API/centres/ -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"name\": \"Example Diagnostics $(date +%s)\", \"location\": \"Delhi\"}" | get "['id']")
```

**4. Create a test with preparation details** (admin)

```bash
TEST_ID=$(curl -s -X POST $API/tests/ -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"name\": \"Example Blood Panel $(date +%s)\", \"description\": \"Demo\", \"base_price\": \"500.00\",
       \"sample_type\": \"BLOOD\", \"fasting_hours\": 10, \"report_turnaround_hours\": 24,
       \"preparation_instructions\": \"Fast for 10 hours; water is fine.\"}" | get "['id']")
```

**5. Offer the test at the centre with a centre-specific price** (admin)

```bash
curl -s -X POST $API/centres/$CENTRE_ID/tests -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"test_id\": $TEST_ID, \"price\": \"750.00\"}"
# 201 {"id":..,"centre_id":..,"centre_name":"Example Diagnostics ...","test_id":..,"test_name":"...","price":"750.00",...}
```

**6. Compare prices across centres**

```bash
curl -s "$API/tests/$TEST_ID/centres"
# {"items":[{"centre_name":"Example Diagnostics ...","price":"750.00",...}],"total":1,"page":1,"page_size":20,"pages":1}
```

**7. Create a booking.** The `amount` sent here is deliberately wrong, and the server ignores it:

```bash
BOOKING=$(curl -s -X POST $API/bookings/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"test_id\": $TEST_ID, \"centre_id\": $CENTRE_ID, \"appointment_datetime\": \"$(future 7 10:30)\", \"amount\": 1}")
echo "$BOOKING"
# 201 {"id":1,...,"test_name":"Example Blood Panel ...","amount":"750.00","status":"PENDING",
#      "payment_in_progress":false,"hold_expires_at":"<created_at + 30 min>","changeable_until":"<appointment - 24 h>",...}
BOOKING_ID=$(echo "$BOOKING" | get "['id']")
```

**8. See which times are already taken** (public; returns times only, no patient details)

```bash
curl -s "$API/centres/$CENTRE_ID/tests/$TEST_ID/booked-times?start=$(future 7 00:00)&end=$(future 8 00:00)"
# {"centre_id":..,"test_id":..,"start":"...","end":"...","booked":["...T10:30:00Z"]}
```

**9. Pay for the booking**

```bash
curl -s -X POST $API/payments/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"booking_id\": $BOOKING_ID}"
# 201 {"id":1,"booking_id":1,"provider":"simulated","provider_payment_id":"pay_9f2c...",
#      "amount":"750.00","status":"SUCCESS","booking_status":"CONFIRMED",...}
```

**10. Get bookings and payment history**

```bash
curl -s "$API/bookings/?status=CONFIRMED&page=1&page_size=20" -H "Authorization: Bearer $TOKEN"
curl -s "$API/bookings/$BOOKING_ID" -H "Authorization: Bearer $TOKEN"
curl -s "$API/bookings/$BOOKING_ID/payments" -H "Authorization: Bearer $TOKEN"
# [{"id":1,"booking_id":1,...,"status":"SUCCESS","booking_status":"CONFIRMED",...}]
```

**11. Reschedule the confirmed booking** (keeps its price and payment)

```bash
curl -s -X PATCH $API/bookings/$BOOKING_ID/reschedule -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"appointment_datetime\": \"$(future 9 14:00)\"}"
# 200 {...,"appointment_datetime":"...T14:00:00Z","status":"CONFIRMED",...}
```

**12. Cancel bookings.** A `PENDING` booking is simply cancelled. A `CONFIRMED` booking is
refunded; the patient can do this until `CANCELLATION_WINDOW_HOURS` (default 24) before the
appointment, after which it returns 403 *"Confirmed bookings can only be cancelled online up to
24 hours before the appointment. Please contact the centre."* and only an admin can cancel it.

```bash
PENDING_ID=$(curl -s -X POST $API/bookings/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"test_id\": $TEST_ID, \"centre_id\": $CENTRE_ID, \"appointment_datetime\": \"$(future 8 11:00)\"}" | get "['id']")
curl -s -X PATCH $API/bookings/$PENDING_ID/cancel -H "Authorization: Bearer $TOKEN"
# 200 {...,"status":"CANCELLED",...}
curl -s -X PATCH $API/bookings/$BOOKING_ID/cancel -H "Authorization: Bearer $TOKEN"
# 200 {...,"status":"CANCELLED",...}   ← 9 days ahead, so the patient may cancel; the payment is refunded
curl -s "$API/bookings/$BOOKING_ID/payments" -H "Authorization: Bearer $TOKEN"
# [{...,"status":"REFUNDED","booking_status":"CANCELLED",...}]
```

**13. Admin reporting and user management**

```bash
curl -s "$API/admin/stats?days=30" -H "Authorization: Bearer $ADMIN"
# {"days":30,...,"bookings_total":..,"bookings_by_status":{"PENDING":0,"CONFIRMED":0,...},
#  "revenue":"...","refunded":"750.00","payment_success_rate":1.0,...,"centres":[...],"top_tests":[...]}
curl -s "$API/admin/users?search=john" -H "Authorization: Bearer $ADMIN"
# {"items":[{"id":..,"name":"John Doe","email":"john...@example.com","role":"USER","is_active":true,"booking_count":2,...}],...}
```

**14. Payment webhook.**

1. Switch to `PAYMENT_SIMULATION_MODE=pending` so payments wait for the webhook: edit `.env`,
   then run `docker compose up -d api maintenance`.
2. Create a booking, then pay. The payment comes back `PENDING`.
3. Send the signed webhook. The signature is the hex HMAC-SHA256 of the **exact request body**,
   keyed with `WEBHOOK_SECRET`.

```bash
WEBHOOK_SECRET=$(grep '^WEBHOOK_SECRET=' .env | cut -d= -f2-)
BOOKING_ID=$(curl -s -X POST $API/bookings/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"test_id\": $TEST_ID, \"centre_id\": $CENTRE_ID, \"appointment_datetime\": \"$(future 10 12:00)\"}" | get "['id']")
PAYMENT_ID=$(curl -s -X POST $API/payments/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"booking_id\": $BOOKING_ID}" | get "['provider_payment_id']")          # status PENDING

BODY="{\"event_id\":\"evt_$(date +%s)\",\"payment_id\":\"$PAYMENT_ID\",\"booking_id\":$BOOKING_ID,\"status\":\"SUCCESS\",\"amount\":750}"
SIG=$(python -c "import hmac, hashlib, sys; print(hmac.new(sys.argv[1].encode(), sys.argv[2].encode(), hashlib.sha256).hexdigest())" "$WEBHOOK_SECRET" "$BODY")
# (alternative with openssl: SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | sed 's/^.*= //'))

curl -s -X POST $API/payments/webhook/ -H 'Content-Type: application/json' -H "X-Webhook-Signature: $SIG" -d "$BODY"
# 200 {"event_id":"evt_...","outcome":"APPLIED","duplicate":false,"detail":"Payment SUCCESS; booking CONFIRMED"}
curl -s -X POST $API/payments/webhook/ -H 'Content-Type: application/json' -H "X-Webhook-Signature: $SIG" -d "$BODY"
# 200 {"event_id":"evt_...","outcome":"APPLIED","duplicate":true,"detail":"Event already processed"}   ← idempotent
curl -s "$API/admin/webhook-events?page_size=5" -H "Authorization: Bearer $ADMIN"
# {"items":[{"event_id":"evt_...","outcome":"APPLIED","payload":{...},...}],"total":..,...}
```

**15. Errors look like this**

```bash
curl -s $API/bookings/                                                    # no token
# 401 {"detail":"Not authenticated"}
curl -s -X POST $API/payments/webhook/ -H 'Content-Type: application/json' -d "$BODY"   # unsigned
# 401 {"detail":"Invalid webhook signature"}
curl -s -X POST $API/centres/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "X", "location": "Y"}'                                     # not an admin
# 403 {"detail":"Admin privileges required"}
```

Remember to set `PAYMENT_SIMULATION_MODE` back to `success` afterwards (edit `.env`, then
`docker compose up -d api maintenance`).

---

## 10. Booking lifecycle

```
                   payment SUCCESS                 appointment passed, admin records
             ┌──────────────────────► CONFIRMED ──────────────► COMPLETED / NO_SHOW
             │                            │
             │                            │ cancel: patient before the cutoff,
             │                            │ admin any time (payment refunded)
             │                            ▼
 PENDING ────┼──────────────────────► CANCELLED
             │  cancel (owner or admin)
             │
             ├──────────────────────► FAILED     payment FAILED
             │
             └──────────────────────► EXPIRED    unpaid past BOOKING_HOLD_MINUTES
```

- Every booking starts as `PENDING`. `FAILED`, `CANCELLED`, `EXPIRED`, `COMPLETED` and `NO_SHOW`
  are **terminal**.
- **Unpaid holds expire.** A `PENDING` booking with no payment in progress holds its slot for
  `BOOKING_HOLD_MINUTES` (default 30). After that it becomes `EXPIRED` and the slot is free:
  - lazily, when someone else books that exact slot (inside the same transaction, so concurrent
    bookers still can't both get it);
  - when the owner tries to pay it (409, "reservation has expired");
  - and in bulk by the maintenance job (`app/scripts/maintenance.py`, run every minute by the
    `maintenance` Compose service, or `POST /admin/maintenance/run`).

  The partial unique index can't depend on the current time, so expiry really changes the row's
  status rather than being computed at read time. `GET …/booked-times` does treat lapsed holds as
  free, so the UI doesn't show them as taken in the meantime. Bookings expose `hold_expires_at`
  and `payment_in_progress`.
- **Cancellation and rescheduling rules**:
  - Anyone can cancel or reschedule their own `PENDING` booking (unless a payment is in progress).
  - A `CONFIRMED` booking can be cancelled (full refund, payment → `REFUNDED`) or rescheduled by
    the patient until `CANCELLATION_WINDOW_HOURS` (default 24) before the appointment, and by an
    admin at any time. Bookings expose this cutoff as `changeable_until`.
  - Rescheduling keeps the booking's price and payment; the new time is protected by the same
    partial unique index.
- **Attendance**: once the appointment time has passed, an admin marks a `CONFIRMED` booking
  `COMPLETED` or `NO_SHOW`.
- Transitions are defined in one table (`BOOKING_TRANSITIONS` in `app/models/booking.py`).
  Anything else raises `InvalidStateTransitionError` (HTTP 409), for example
  `CONFIRMED → PENDING`, `FAILED → CONFIRMED` or `COMPLETED → CANCELLED`.
- Clients never set a status directly. Status changes only come from the cancel, reschedule and
  attendance endpoints, a payment result, a webhook, or expiry.
- A booking whose payment is still `PENDING` can't be cancelled, rescheduled or expired.
  Otherwise the provider could report success for a booking that no longer holds its slot.
- Payments have their own state machine: `PENDING → SUCCESS | FAILED` and `SUCCESS → REFUNDED`.
  `FAILED` and `REFUNDED` are terminal, so a late `SUCCESS` webhook can't re-confirm a refunded
  payment; it gets 409.

---

## 11. Payments

`POST /payments/ {"booking_id": 1}` does the following:

1. Authenticates the user and loads the booking **with a row lock** (`SELECT … FOR UPDATE`),
   filtered by owner. Another user's booking returns 404.
2. Checks that the booking is payable: it must be `PENDING`, its appointment must still be in
   the future, and it must have no payment in progress. Otherwise the request fails with 409.
3. Charges **`booking.amount`**. The request has no amount field.
4. Creates the `payments` row and applies the result **in the same transaction**:
   - `SUCCESS` → booking `CONFIRMED`
   - `FAILED` → booking `FAILED`
   - `PENDING` → the booking is unchanged until a webhook arrives.

The simulated provider (`app/services/payment_provider.py`) is selected by
`PAYMENT_SIMULATION_MODE`:

| Mode | Outcome |
|---|---|
| `success` (default) | Every charge succeeds. |
| `failure` | Every charge fails. |
| `random` | Succeeds with probability `PAYMENT_SUCCESS_RATE`; accepts a seeded RNG for reproducible tests. |
| `pending` | The charge stays `PENDING`; the final result arrives through the webhook (asynchronous flow). |

The provider is a FastAPI dependency (`get_payment_provider`), so tests swap in a deterministic
one.

**Duplicate payments are impossible**:

- The booking row lock serialises concurrent attempts, so the second attempt sees the first
  attempt's result.
- The partial unique index `uq_payments_active_booking` allows at most one `PENDING`/`SUCCESS`
  payment per booking at the database level.
- A concurrency test fires five simultaneous payment attempts and checks that exactly one
  succeeds.

**Payment history**: `GET /bookings/{id}/payments` lists every attempt (including failed and
refunded ones) for the owner or an admin.

**Stuck payments are reconciled.** If a `PENDING` payment's webhook never arrives, the booking
can't be paid again, cancelled or expired. `POST /admin/payments/{id}/reconcile` (and the
maintenance job, for payments pending longer than `PAYMENT_PENDING_TIMEOUT_MINUTES`) asks the
provider for the charge's status (`PaymentProvider.get_status`) and:

- applies a final `SUCCESS`/`FAILED` result exactly as a webhook would;
- or, if the provider still says `PENDING` after the timeout, treats the charge as abandoned and
  marks it `FAILED`, which releases the booking.

If the provider later reports `SUCCESS` for an abandoned payment, that webhook is rejected
(`FAILED` is final) and appears as `REJECTED` in the admin webhook log for manual follow-up.
The simulated provider keeps no state, so its `get_status` answers according to the current
simulation mode (still `PENDING` in `pending` mode).

---

## 12. Webhook idempotency

`POST /payments/webhook/` receives provider events like this one:

```json
{"event_id": "evt_123456", "payment_id": "pay_123", "booking_id": 123, "status": "SUCCESS", "amount": 750}
```

### 1. Authenticate first

The request must carry an `X-Webhook-Signature` header: the hex HMAC-SHA256 of the raw body,
keyed with `WEBHOOK_SECRET`. The signature is compared in constant time. This check runs
**before** the event is parsed or stored, so:

- nobody can forge a `SUCCESS` event to confirm a booking without paying, and
- unauthenticated callers can't claim `event_id`s ahead of the real provider.

### 2. Claim the event (the idempotency key)

The whole event is processed in **one database transaction**. Its first statement is:

```sql
INSERT INTO webhook_events (event_id, ...) VALUES (...)
ON CONFLICT (event_id) DO NOTHING
RETURNING id;
```

`webhook_events.event_id` has a **UNIQUE constraint**, so only one transaction can ever insert a
given event:

- **A row is returned**: this request owns the event and processes it.
- **Nothing is returned**: the event was already processed. The endpoint answers
  `200 {"duplicate": true, "outcome": <original outcome>}` and does not re-process it.
- **Two identical requests arrive at the same moment**: PostgreSQL makes the second `INSERT`
  wait on the unique index until the first transaction finishes.
  - If the first **commits**, the second gets a conflict and treats the event as a duplicate.
  - If the first **rolls back** (it crashed), the second insert succeeds and processes the event.

  Either way the event is applied exactly once, with no application-level locks.

### 3. Validate and apply (under row locks, inside a savepoint)

```
lock booking (FOR UPDATE)  → 404 "Booking not found"
lock payment (FOR UPDATE)  → 404 "Payment not found"
payment.booking_id == booking_id?        → 400 otherwise
payload.amount == payment.amount?        → 400 otherwise (logged as a warning)
payment already in the reported status?  → NO_OP (200)
payment.transition_to(status)            → 409 if invalid, e.g. SUCCESS → FAILED
booking.transition_to(CONFIRMED/FAILED)  → 409 if invalid, e.g. booking CANCELLED
```

- Locks are always taken **booking first, then payment**. Payment creation and cancellation use
  the same order, so these operations can't deadlock each other.
- The transitions run inside a **SAVEPOINT**. If the booking transition fails after the payment
  transition succeeded, both are rolled back, so there are no partial updates.

### 4. Record the outcome and commit

The event row is marked `processed = true` and gets its `outcome` (`APPLIED`, `NO_OP` or
`REJECTED`), `outcome_detail` and `processed_at`. The transaction then commits.

**Rejected events are stored too**, together with the reason, as an audit trail of suspicious or
invalid events. Their first delivery returns the 4xx error. Redeliveries are acknowledged with
`200 duplicate: true, outcome: REJECTED`, so the provider stops retrying.

### What happens when…

| Scenario | Behaviour |
|---|---|
| The DB transaction fails midway | Everything rolls back, **including the event claim**. The client gets a generic 500, and the provider's retry processes the event from scratch. (Tested by injecting a crash.) |
| The webhook is retried (same `event_id`) | 200 `duplicate: true`, with no state change and no new rows, even if the retried payload differs. |
| Two identical webhooks arrive together | Exactly one applies. The other waits on the unique index, then returns `duplicate: true`. (Tested with 10 concurrent threads, plus a deterministic blocked-then-released test.) |
| The booking has already been cancelled | 409 `Booking cannot transition from CANCELLED to CONFIRMED`. The payment change is rolled back and the event is stored as `REJECTED`. |
| The payment has already succeeded | Same status with a new `event_id`: 200 `NO_OP`. Opposite status (`FAILED`): 409 invalid transition, and nothing changes. |
| The webhook reports a different amount | 400 `Amount does not match the payment amount`, logged as a warning. Booking and payment are untouched, and the event is stored as `REJECTED`. |
| The payment does not exist | 404 `Payment not found`; the event is stored as `REJECTED`. |
| The booking does not exist | 404 `Booking not found`; the event is stored as `REJECTED`. |
| The payment belongs to a different booking | 400; the event is stored as `REJECTED`. |
| The signature is missing or invalid | 401; nothing is stored. |
| The payload is malformed | 422; nothing is stored. |

---

## 13. Authorization & security

- **Roles**: `USER` (the default) and `ADMIN`.
  - Public signup always creates a `USER`; a `role` field in the request is ignored.
  - Admins are created with the command-line script
    `python -m app.scripts.create_admin --email ...`. It reads the password from `ADMIN_PASSWORD`
    or prompts for it, so the password never appears in shell history. It can also promote an
    existing user.
- **Catalogue** (centres, tests, prices): anyone can read it, but only admins can change it.
  Other users get 403, and requests without a token get 401.
- **Bookings**: the owner is always the authenticated user from the JWT; the client can't supply
  a user ID.
  - Queries are **scoped by owner in SQL** (`WHERE id = :id AND user_id = :current_user`).
  - Another user's booking returns **404, not 403**, so an attacker can't even confirm that a
    booking ID exists (IDOR protection).
  - Admins can list and view any booking, cancel or reschedule any active one (confirmed ones
    are refunded when cancelled), and record attendance.
- **User management**: admins can deactivate/reactivate accounts (a deactivated user's existing
  tokens stop working immediately) and grant or remove the admin role. They can't demote or
  deactivate themselves.
- **Booked times** are public but contain only times, never patient details.
- **Payments**: only the booking's owner can pay, *even if the caller is an admin*.
- **JWT**:
  - Tokens are signed with HMAC (HS256 by default) using a secret of at least 32 characters.
  - A valid token must contain `exp`, `iat` and `sub`, plus `type=access`.
  - The user is reloaded from the database on every request, so deactivated users are
    rejected immediately.
- **Passwords**: stored as bcrypt hashes. The login endpoint gives the same error for an unknown
  email and a wrong password. For unknown emails it also runs a dummy bcrypt check, so response
  timing doesn't reveal which emails are registered.
- **SQL injection**: all queries are parameterised by SQLAlchemy. Search input also has its LIKE
  wildcards escaped.
- **Secrets**: configuration comes only from environment variables. Nothing secret is committed,
  and the Docker image doesn't contain `.env`.
- **Container**: the API runs as a non-root user.

---

## 14. Edge cases handled

- **Emails** are normalised (trimmed and lower-cased), so `John@Example.com` and
  `john@example.com` are the same account. Duplicate signups get 409, including concurrent ones
  (unique constraint).
- **Password policy**: at least 8 characters, not blank, not on a common-password denylist, and
  at most 72 bytes (bcrypt's limit), so passwords are never silently truncated.
- **Amounts**: a booking's amount always comes from `centre_tests.price`, and any `amount`,
  `status` or `user_id` sent by the client is ignored. Later price changes don't affect existing
  bookings.
- **Appointment times**:
  - Rejected with 400 if in the past or more than 365 days ahead.
  - Malformed values and numeric Unix timestamps are rejected with 422.
  - Times without a timezone are treated as UTC, and offsets are normalised to UTC.
- **Unknown or unavailable tests**: an unknown test or centre gets 404. A test the centre doesn't
  offer gets 400.
- **Double booking** of the same centre, test and time gets 409. It is enforced by a partial
  unique index, so it is race-safe (tested with five concurrent requests). Cancelled and failed
  bookings release the slot.
- **Invalid IDs**: non-positive or non-numeric IDs get 422; well-formed but unknown IDs get 404.
- **Deleting** a centre or test that has bookings gets 409. Its price entries are cascade-deleted
  with it.
- **PATCH** endpoints reject an explicit `null` for required fields.
- **Search filters** match `%` and `_` literally.
- **Late payments**: a payment is refused once the appointment time has passed.

---

## 15. Assumptions

1. **Slots.** A slot is the exact combination of centre, test and `appointment_datetime`, and it
   can hold one active booking.
   - The service doesn't model opening hours, slot length or capacity. Any future time up to 365
     days ahead can be booked. The UI suggests half-hourly times between 07:00 and 19:00 in the
     patient's time zone and crosses out taken ones, but any exact time can still be chosen.
   - An unpaid `PENDING` booking holds its slot for `BOOKING_HOLD_MINUTES`, then expires.
2. **One successful payment per booking.** `FAILED` is a terminal state for a booking, as the
   given state machine requires, so after a failed payment the user creates a new booking (the
   UI's *Book again* button pre-fills the same test and centre). The schema does allow several
   *failed* payments per booking, in case retries are added later.
3. **Cancelling a `CONFIRMED` booking always refunds the full amount** (simulated refunds always
   succeed). Patients can do it, or reschedule, until `CANCELLATION_WINDOW_HOURS` before the
   appointment; after that they have to contact the clinic, and only an admin can change it.
   Nobody can cancel a booking while its payment is still being processed. There are no partial
   refunds or cancellation fees.
4. **Payments have a `PENDING` status** as well as `SUCCESS` and `FAILED`. This is what enables
   the asynchronous, webhook-driven flow. In the default `success` mode, payments complete
   immediately, and a later webhook for them is a harmless `NO_OP`.
5. **Webhooks are authenticated** with a shared-secret HMAC over the raw body. The task didn't
   ask for this, but without it anyone could confirm bookings for free. Real payment providers
   authenticate their webhooks the same way.
6. **The webhook's `event_type`** is derived from `status` (`payment.success` or
   `payment.failed`) rather than sent separately.
7. **Rejected webhook events are consumed.** Their `event_id` is recorded, and a redelivery is
   acknowledged rather than re-evaluated. A provider must send any correction under a new
   `event_id`.
8. **The catalogue can be read without logging in**, so patients can browse centres, tests and
   prices before signing up.
9. **Money.**
   - Amounts are returned as strings (`"750.00"`).
   - Webhook amounts can be sent as numbers or strings and are compared as exact decimals, so
     `750` equals `"750.00"`.
   - The currency is implicitly INR; there is no currency column.
10. **Admins are provisioned out-of-band.** There is no API for creating admins or deleting
    users. Admins manage bookings but don't pay on a patient's behalf.
11. **Authentication uses access tokens only.** They last 60 minutes by default, and there are no
    refresh tokens.
12. **`GET /users/me/bookings` isn't a separate endpoint**, because `GET /bookings/` is already
    limited to the caller's bookings. `GET /auth/me` returns the user's profile.
13. **Tests need PostgreSQL.** SQLite can't reproduce the row locks, partial indexes and
    `ON CONFLICT` behaviour the tests exercise.

---

## 16. Testing

**One-time setup.** Start the database, create the test database, and install the dependencies
into a virtual environment:

```bash
docker compose up -d db
docker compose exec db psql -U postgres -c "CREATE DATABASE diagnostic_booking_test"
python -m venv .venv && source .venv/bin/activate      # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Run the tests** (takes about 30 seconds):

```bash
pytest
```

- The tests use `TEST_DATABASE_URL`, which defaults to `diagnostic_booking_test` on localhost.
  Its tables are **emptied after every test**, so never point it at data you care about.
- The schema is built by running the real **Alembic migrations**, so every test run checks the
  migrations too.
- API tests go through FastAPI's `TestClient`. Concurrency tests use real threads, each with its
  own database session, against PostgreSQL.

**277 tests, all passing:**

| File | Tests | Covers |
|---|---|---|
| `test_auth.py` | 28 | signup, duplicate/case-insensitive email, invalid email, weak passwords, role can't be self-assigned, login, wrong credentials, missing/invalid/expired/forged JWT, deactivated user |
| `test_centres.py` | 20 | CRUD, validation, pagination, filters (including a literal `%`), invalid IDs, error shape, delete blocked by bookings, users and anonymous callers can't modify |
| `test_tests.py` | 29 | CRUD, price validation, search, centre–test association, default price, **different prices at different centres**, duplicate offering, cascade on centre delete, preparation details (round trip, validation, clearing with `null`) |
| `test_bookings.py` | 99 | creation, **server-side amount** (tampered fields ignored), price snapshot, time zones, invalid test/centre, test not offered, past/far-future/malformed times, invalid IDs, double booking (including **5 concurrent requests**), IDOR (read and cancel), admin visibility, patient details, status filter, cancellation, **cancel & refund of confirmed bookings** (patients before the cutoff, admins any time; patients get 403 inside the window), refunded bookings can't be paid again, invalid transitions, **every state transition checked** |
| `test_payments.py` | 23 | success, failure, pending, amount taken from the booking, invalid booking, another user's booking (including admins), unauthenticated, duplicate payment after success or while pending, failed/cancelled/past bookings not payable, **5 concurrent payment attempts → 1 payment**, provider modes, seeded random mode |
| `test_webhooks.py` | 31 | SUCCESS/FAILED, **the same webhook ×2 and ×10**, replay with an altered payload, new event → `NO_OP`, **10 concurrent duplicates → applied once**, **deterministic in-flight duplicate blocking**, competing SUCCESS and FAILED events, **crash mid-processing → nothing recorded, retry works**, amount mismatch (and its log warning), rejected-event redelivery, unknown payment/booking, payment/booking mismatch, invalid transitions, late SUCCESS for a refunded payment rejected, cancelled booking with no partial update, malformed payloads, missing/forged/mismatched signatures |
| `test_lifecycle.py` | 26 | response fields (names, `hold_expires_at`, `changeable_until`, `payment_in_progress`), **unpaid-hold expiry** (lazy on rebooking, on payment, in bulk, disabled with 0, never with a payment in progress, **5 concurrent bookers of an expired slot → 1 wins**), **rescheduling** (pending/confirmed, keeps payment, frees old slot, taken/past times, cutoff, IDOR), **attendance** (completed/no-show, only after the appointment, admin only), booked times, payment history |
| `test_admin.py` | 18 | admin-only access, **stats** (counts, revenue, refunds, success rate, per-centre, top tests, empty system), **users** (search, counts, deactivate/reactivate, promote, can't lock yourself out), **webhook ledger** filters, **reconciliation** (provider settles, too early → 409, timeout → FAILED, late SUCCESS rejected and logged), maintenance run |
| `test_ui.py` | 3 | `/` redirects to the UI, UI assets are served, the UI doesn't hide any API routes |

**Lint and format** (configuration in `pyproject.toml`):

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
alembic check          # the models and migrations are in sync
```

---

## 17. What I would improve with more time

**Product**

- **Appointment slot management**: centre opening hours and time zones, slot lengths and
  per-slot capacity, instead of treating every exact timestamp as a separate slot. This would
  replace the partial unique index with a capacity check under a row lock.
- **Partial refunds / cancellation fees**, and cancellation reasons shown to the patient.
- **Results delivery**: attach a report to a `COMPLETED` booking, visible only to the patient
  (needs file storage).
- **Multi-test bookings** (one visit, several tests, one payment).
- **A booking change history** (who changed what, when) to complement the webhook ledger.
- **Notifications**: email or SMS confirmations and reminders, sent through a background worker.
- **Currency column** and soft deletes for the catalogue, to keep price history.

**Payments and reliability**

- **A real payment provider** (Stripe or Razorpay):
  - Create the payment intent *outside* the database transaction.
  - Send idempotency keys on outgoing charge requests.
  - Verify the provider's own webhook signatures, including a timestamp tolerance to limit
    replay windows.
- **An `Idempotency-Key` header** on `POST /payments/` and `POST /bookings/`, so a client retry
  gets the original response instead of a 409.
- **Celery or another task queue**: acknowledge webhooks quickly and process them in the
  background with retries. The `processed` flag already supports this.
- **Distributed locking**, but only if work moves outside PostgreSQL. Today's guarantees come
  from database constraints and row locks, which already hold across multiple API instances.

**Security**

- **Rate limiting** on login, signup and webhooks, for example with Redis.
- **Auth hardening**:
  - Refresh tokens and token revocation.
  - Email verification, password reset, and lockout after repeated failed logins.
  - In the UI, an httpOnly cookie instead of `localStorage` for the token.

**Operations and quality**

- **Observability**:
  - Structured JSON logs with request IDs.
  - Prometheus metrics, such as payment success rate and webhook rejections.
  - OpenTelemetry tracing.
  - A readiness probe that checks the database.
- **A CI pipeline** running ruff, `alembic check` and pytest against a PostgreSQL service, plus
  test coverage reports.
- **Pinned dependencies** in a lock file (for example with `uv`) for reproducible builds.
  `requirements.txt` currently uses version ranges.
- **Browser end-to-end tests** for the UI (Playwright), and **API versioning** (`/v1`) before any
  breaking change.
- **Caching** of catalogue reads, for example with Redis.
