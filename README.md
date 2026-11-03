# mi-health-mcp

Local-first Xiaomi health data hub for agents: **REST + MCP + scheduled sync**.

This is a clean rebuild of the ideas from the community `mi-fitness-mcp` projects. It is not
affiliated with Xiaomi. It reads **your own** health data from the Xiaomi cloud and stores it in a
local SQLite database for analysis by agents.

> Status: data plane + admin UI implemented. REST/MCP serve the same query layer; the React admin
> console covers login, overview, charts, keys, sync status and QR login.

## What it does

- Syncs Xiaomi Mi Fitness cloud data on a schedule (steps/HR/SpO2/stress every 30 minutes, sleep
  every 2 hours, workouts/body every 12 hours by default).
- Stores data in SQLite with WAL and batched transactions.
- Serves the same query layer through:
  - REST `/api/*`
  - MCP Streamable HTTP `/mcp`
- Returns freshness metadata (`data_as_of`, `last_synced_at`, `stale`) with every response.
- Uses read-only agent API keys (optional `sync` scope); admin operations are separate.

## Architecture

```text
Internet
  -> reverse proxy / TLS
  -> 127.0.0.1:8330
      FastAPI (REST / SPA / MCP)
      worker  (scheduler + backfill + sync)
      SQLite WAL (/data)
```

The API and worker are separate processes in the same container. The API never performs a Xiaomi
sync, so a slow RC4/JSON/SQLite batch cannot block agent queries.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

# 1. Generate a master key for encrypted credentials
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
export MI_HEALTH_MASTER_KEY='...'

# 2. Admin login + session secret (hash is Argon2id; prompt is interactive)
export MI_HEALTH_ADMIN_PASSWORD_HASH="$(mi-health admin hash-password)"
export MI_HEALTH_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

# 3. Store your Xiaomi userId/passToken, or use the admin UI QR login
mi-health set-credentials --user-id "<userId>" --pass-token "<passToken>" --region cn

# 4. Create a read-only agent key (shown once)
mi-health key create --label agent --scopes read

# 5. Run API and worker (separate processes)
mi-health serve-api --host 127.0.0.1 --port 8330
mi-health serve-worker
```

Open `http://127.0.0.1:8330/` for the admin console. It uses a session cookie, not an agent API key.
The bottom of the overview shows whether data is stale; use **同步** for an on-demand refresh and
**扫码登录** to authorize Xiaomi.

MCP client URL: `http://127.0.0.1:8330/mcp/` with `Authorization: Bearer mhk_...` (the mounted Streamable HTTP endpoint redirects `/mcp` to `/mcp/`).

Frontend development/build:

```bash
cd web
bun install
bun run typecheck
bun run build          # -> web/dist
# run the API with the built UI
MI_HEALTH_WEB_DIR=web/dist mi-health serve-api --host 127.0.0.1 --port 8330
```
REST example:

```bash
curl 'http://127.0.0.1:8330/api/summary?start_date=2026-11-01&end_date=2026-11-03' \
  -H 'Authorization: Bearer mhk_...'
```

## Docker

```bash
cp .env.example .env          # set MI_HEALTH_MASTER_KEY and MI_HEALTH_ALLOWED_HOSTS
docker compose -f docker-compose.example.yml up -d --build
mi-health key create --label agent --scopes read   # from inside the container or the CLI
```

The example binds `127.0.0.1:8330` on the host; expose it through a TLS reverse proxy and set
`MI_HEALTH_ALLOWED_HOSTS` to the public host, otherwise MCP's DNS-rebinding protection rejects the
Host header. Set `MI_HEALTH_HTTPS_ONLY=1` so the session cookie gets the Secure flag.

## Data notes

- **Sleep HRV is aggregate-only.** Xiaomi's `sleep` payload exposes `avg_hrv`, `min_hrv`,
  `max_hrv`, `hrv_median`, `hrv_std`, quantiles, baseline min/max and an analysis timestamp; there
  is no public time-series HRV endpoint in the old cloud API.
- The cloud is the source of truth. The local DB is a cache; the UI/API report both
  `data_as_of` (cloud record time) and `last_synced_at` (local sync time).
- This project only reads your own data. It is not a Xiaomi-official product.

## Upstream references and credits

This project would not exist without the community's reverse-engineering work. It does not copy
their data or credentials; protocol behavior is reimplemented/adapted with attribution.

- [kubulashvili/mi-fitness-mcp](https://github.com/kubulashvili/mi-fitness-mcp) — original MCP
  server and Xiaomi cloud adapter (MIT), which this project's protocol layer is adapted from.
- [HUAYUE1024/mi-fitness-mcp-cn](https://github.com/HUAYUE1024/mi-fitness-mcp-cn) —
  China-region fork, QR login and data-field work (MIT).
- [kevinkwee/Mi-Fitness-Sync](https://github.com/kevinkwee/Mi-Fitness-Sync) — decompiled auth/FDS
  notes and Python client (MIT).
- [Jenwein/mifit](https://github.com/Jenwein/mifit) — protocol/field notes and probes.
- [ilyasaftr/better-mi-fitness-sync](https://github.com/ilyasaftr/better-mi-fitness-sync) —
  sleep HRV field semantics/units (Kotlin client).
- [AlexxIT/SmartScaleConnect](https://github.com/AlexxIT/SmartScaleConnect) —
  Go Xiaomi signed-request reference.
- [motor-screwdriver/advx2026-project](https://github.com/motor-screwdriver/advx2026-project) —
  Go MiFitness login/RC4/sleep client reference.

No Xiaomi code or assets are included. The protocol is unofficial and may break.

## Security

- Never commit `.env`, `MI_HEALTH_MASTER_KEY`, API keys, or `passToken`.
- `passToken` is encrypted at rest with a master key from the environment.
- API keys are stored as hashes and shown only once.
- Bind the app to `127.0.0.1` and expose it through a TLS reverse proxy.
- Read the [requirements](docs/REQUIREMENTS.md) for the agreed threat model.

## License

MIT. See [LICENSE](LICENSE).
