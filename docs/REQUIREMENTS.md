# Requirements baseline

Agreed on 2026-11-03. This document is the source of truth for the rebuild; update it before
changing scope.

## Goal

Provide a stable **REST + MCP** interface to Xiaomi health data for an agent doing health
analysis. The UI is a low-frequency admin/observability surface, not an analysis workbench.

## Non-goals (v1)

- Sleep HRV time series (upstream exposes overnight aggregates only).
- Multi-account / multi-user / SaaS.
- Real-time BLE steps from the phone app.
- Full history / per-second workout detail (FDS).
- Notifications (Telegram/email/webhook).
- Frontend API debugger / full dashboard.
- Go rewrite (v1 is Python).
- SSE MCP transport (Streamable HTTP only).
- In-place migration of the old schema.

## Decisions

| Area | Decision |
| --- | --- |
| Source of truth | Xiaomi cloud; local SQLite cache |
| Freshness | high-frequency 30 min (incl. sleep), other 12 h; stale = 2× interval |
| Scope | single user |
| Backend | Python + FastAPI + official MCP SDK |
| Topology | one container, `api` + `worker` processes, shared SQLite WAL |
| Storage | SQLite + WAL; new schema; `sync_state(user_id,data_type)`, `sync_runs`, `sync_jobs` |
| MCP | Streamable HTTP at `/mcp`; REST at `/api/*`; same query layer |
| Envelope | `{status, data, meta:{count,data_as_of,last_synced_at,stale,next_sync_at}, error?}` |
| Tools | existing 14 domain tools + freshness; `sync` only with `sync` scope |
| Agent keys | `read` / `sync`; read 60 req/min; sync 6/hour; revocable; hashed |
| Admin auth | password (Argon2id) + HttpOnly/SameSite session + CSRF |
| Credentials | QR login; `passToken` encrypted (AES-GCM/Fernet) with env master key |
| Backfill/retention | 90 days backfill; raw samples 180 days; daily/sleep/HRV forever |
| Failures | cached data + stale/error; exponential backoff; auth failure stops retries; no notifications |
| Migration | fresh DB + 90-day backfill; old keys invalidated; old container kept for rollback |
| Cutover | app binds 127.0.0.1:8330; reverse proxy 443; old 8321/8322 kept for rollback |
| UI | React + TS + Vite + TanStack Query + ECharts; minimal admin + basic charts |

## Acceptance criteria

1. Agent end-to-end: public `/mcp` + `/api` with a read key can query 90 days; MCP tools list/call;
   `meta` contains `data_as_of`, `last_synced_at`, `stale`.
2. Sync/freshness: 30 min / 12 h cadence; backoff; auth-failure state; stale consistent in
   API and UI.
3. Backfill/retention/recovery: 90-day backfill resumes after interruption; 180-day raw retention;
   API stays available during backfill.
4. Admin UI: password login; key create/list/revoke; overview cards; basic charts (server-side
   aggregation); sync status/manual refresh; no debugger.
5. Security/isolation: app only on loopback; reverse proxy TLS; session + CSRF; hashed keys;
   encrypted credentials; rate limits.
6. Non-blocking + tests: api/worker split; protocol parity, sync integration, API/MCP contract,
   UI smoke tests.

## Open design items

- Exact JSON Schemas/descriptions for the 14 tools; remove `profile` if no data source exists.
- Chart page composition and colors.
- Sync job concurrency (single-flight per type, global cap).
- Session storage and password reset.
- QR login details and credential rotation.
- Mock Xiaomi fixtures and parity test approach.
