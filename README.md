# Event-Driven Order Platform

[![CI](https://github.com/NikitaManasa/order-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/NikitaManasa/order-platform/actions/workflows/ci.yml)

A microservices order-processing backend in Python. Four services coordinate through
**RabbitMQ events** (the choreographed saga pattern), and a fifth issues **JWT access tokens**.
There are no direct service-to-service calls, each service owns its own **PostgreSQL** database,
secrets are mounted at runtime rather than stored in code, and every part of the flow is
idempotent and covered by tests.

```mermaid
flowchart LR
    client([Client]) -->|1. login| identity[identity-service]
    identity -. public keys via JWKS .-> orders
    client -->|2. POST /orders + Bearer token| orders
    identity --- udb[(identity DB)]
    subgraph bus [RabbitMQ: topic exchange 'events']
    end
    orders[orders-service] -- order.created --> bus
    bus -- order.created --> inventory[inventory-service]
    inventory -- inventory.reserved / rejected --> bus
    bus -- inventory.reserved --> payments[payments-service]
    payments -- payment.succeeded / failed --> bus
    bus -- payment.failed --> inventory
    bus -- payment.* / inventory.rejected --> orders
    orders -- order.confirmed / cancelled --> bus
    bus -- order.confirmed / cancelled --> notifications[notifications-service]
    orders --- odb[(orders DB)]
    inventory --- idb[(inventory DB)]
    payments --- pdb[(payments DB)]
```

## How an order flows

| Step | Service | Consumes | Does | Publishes |
|---|---|---|---|---|
| 1 | orders | `POST /orders` | saves order as `PENDING` | `order.created` |
| 2 | inventory | `order.created` | checks stock and price; reserves stock | `inventory.reserved` or `inventory.rejected` |
| 3 | payments | `inventory.reserved` | charges the card (simulated limit) | `payment.succeeded` or `payment.failed` |
| 4 | inventory | `payment.failed` | **compensation:** releases the reserved stock | – |
| 5 | orders | `payment.*`, `inventory.rejected` | moves order to `CONFIRMED` / `CANCELLED` | `order.confirmed` / `order.cancelled` |
| 6 | notifications | `order.confirmed` / `order.cancelled` | notifies the customer | – |

## Engineering decisions

- **Choreography instead of a central orchestrator.** Services only know about events, not
  about each other, so a new consumer (for example analytics) can be added without changing
  any existing service.
- **Database per service.** Inventory is the only service that can change stock; other
  services find out about changes through events.
- **Idempotent consumers.** RabbitMQ delivers *at least once*, so the same message can arrive
  twice. Orders and inventory record every processed `event_id`, and payments enforces a unique
  `order_id`, so a redelivered message never double-reserves stock or double-charges a card.
  (Tested in [tests/test_saga.py](tests/test_saga.py).)
- **Retries and dead-letter queues.** A failing handler is retried 3 times. After that, the
  message is moved to `<service>.dlq` so it can be inspected instead of being lost or retried forever.
  (See [shared/common/messaging.py](shared/common/messaging.py).)
- **Server-side price check.** Inventory rejects any order whose prices don't match the catalog,
  so the client can't choose its own price.
- **Deadlock-safe locking.** Stock rows are locked with `SELECT ... FOR UPDATE` in sorted SKU order.
- **Testable core.** The business logic is plain functions (`event in, events out`), and the
  RabbitMQ code is a thin layer around them. That lets the full saga be tested end to end with
  an in-memory bus, with no Docker needed.
- **Structured JSON logs.** Every log line includes `service`, `event_type` and `order_id`,
  so one order can be traced across all four services.

## Security

| Concern | How it's handled |
|---|---|
| **Authentication** | `identity-service` issues short-lived JWTs signed with **RS256**. Other services fetch only the **public** key from `/.well-known/jwks.json`, so a compromised service can check tokens but cannot create them. Keys carry a `kid`, so they can be rotated. |
| **Token validation** | The algorithm is pinned to RS256, and `exp`, `iss` and `aud` are required. Tests in [tests/test_auth.py](tests/test_auth.py) attack this directly: expired tokens, a wrong audience, a wrong signing key, `alg: none`, and the RS256→HS256 key-confusion attack. |
| **Authorization** | Two roles, `customer` and `admin`. Customers see only their own orders, and someone else's order returns **404, not 403**, so order ids can't be probed. The customer id always comes from the token, never the request body. Restocking, payments and notifications are admin-only. |
| **Passwords** | Hashed with **scrypt** and a random salt. A login with an unknown email does a dummy hash, so its response time matches a wrong password and attackers can't tell which emails are registered. Both cases return the same error. Nobody can register themselves as an admin. |
| **Secrets** | No secrets in code or images. Services read `*_FILE` env vars pointing at mounted files ([shared/common/secrets.py](shared/common/secrets.py)). Locally, `scripts/dev_secrets.py` generates random ones into a git-ignored `secrets/` folder. In the cloud, the same files come from AWS Secrets Manager through the External Secrets operator, with no code change. |
| **Containers** | Run as a non-root user, and `secrets/` and tests are excluded from the image. |

## Run it

**Tests (no Docker needed):**
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pytest
.\.venv\Scripts\ruff check .
```

**Full stack (requires Docker Desktop):**
```powershell
.\.venv\Scripts\python scripts/dev_secrets.py   # once: random DB password, admin password, JWT key
docker compose up -d --build
python scripts/demo.py          # saga scenarios + access-control checks against the live stack
docker compose logs -f orders   # follow the JSON logs
docker compose down -v
```

To try it by hand, register at http://localhost:8005/docs and call `/auth/token`. Then click
**Authorize** in any other service's docs and paste the `access_token`. The admin login is
`admin@example.com`, with the password in `secrets/admin_password.txt`.

| Service | URL |
|---|---|
| identity API docs | http://localhost:8005/docs |
| orders API docs | http://localhost:8001/docs |
| inventory API docs | http://localhost:8002/docs |
| payments API docs | http://localhost:8003/docs |
| notifications API docs | http://localhost:8004/docs |
| RabbitMQ UI | http://localhost:15672 (guest / guest, local only) |

## Project layout

```
shared/common/        event envelope, RabbitMQ publisher/consumer, JWT auth, secrets, logging, DB helpers
services/<name>/      one FastAPI service each: models, handlers (business logic), API, tests
services/Dockerfile   single parametrised image (--build-arg SERVICE=...), runs as non-root
tests/                cross-service saga tests and token-security tests
scripts/              local secret generation, end-to-end demo / integration check
infra/                database bootstrap (Terraform + Kubernetes go here next)
```

## Roadmap

- [x] **Phase 1:** microservices, RabbitMQ saga, idempotency, DLQs, Docker Compose, 37 tests
- [x] **Phase 2:** GitHub Actions CI: lint → tests → full-stack integration run in Docker → images pushed to GHCR tagged by commit SHA ([ci.yml](.github/workflows/ci.yml))
- [x] **Phase 3:** identity service, RS256 JWT + JWKS, role-based access, file-mounted secrets, 68 tests
- [ ] Login rate limiting and refresh tokens
- [ ] **Phase 4:** Kubernetes manifests / Helm, deployed to `kind` locally and EKS with Terraform
- [ ] **Phase 5:** Prometheus metrics, Grafana dashboards, OpenTelemetry tracing
- [ ] **Known limitation → transactional outbox:** events are published *after* the DB commit, so a
      crash between those two steps can lose an event. The fix is to write events to an outbox table
      in the same transaction and have a relay publish them.
- [ ] Alembic migrations (tables are currently created on startup)
