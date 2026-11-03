# Deployment

This document describes the loopback + reverse-proxy deployment agreed in
[REQUIREMENTS.md](REQUIREMENTS.md). Never commit `.env`, API keys, or the admin password.

## 1. Container

```bash
docker volume create mi-health-data
docker run -d --name mi-health --restart unless-stopped \
  -p 127.0.0.1:8330:8330 \
  -v mi-health-data:/data \
  --env-file /opt/mi-health/.env \
  ghcr.io/lyizhuo/mi-health-mcp:latest
```

The image contains the API/worker entrypoint and the built React admin console. The host port is
bound to loopback only; the reverse proxy is the public entrypoint.

## 2. Secrets (`/opt/mi-health/.env`, mode 600)

```bash
python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'  # master key
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'                             # session secret
```

Generate the Argon2id admin hash with `mi-health admin hash-password` (interactive), then set:

```dotenv
MI_HEALTH_MASTER_KEY=...
MI_HEALTH_SESSION_SECRET=...
MI_HEALTH_ADMIN_PASSWORD_HASH=$argon2id$...
MI_HEALTH_REGION=cn
MI_HEALTH_ALLOWED_HOSTS=health.example.com,127.0.0.1,127.0.0.1:*
MI_HEALTH_HTTPS_ONLY=1
```

Store the admin password separately (mode 600) if you generate it; do not put it in the repo or
shell history.

## 3. First login and credentials

- Open the reverse-proxied HTTPS URL, log in with the admin password.
- Use **扫码登录** to authorize the Xiaomi account (passToken is encrypted with the master key).
- Create agent keys on the **Key** page (`read`, optionally `sync`); the token is shown once.
- Alternatively, for a one-time migration from an old installation:
  `mi-health set-credentials --user-id ... --pass-token ... --region cn` inside the container.

The worker backfills 90 days on first start (7-day chunks, resumable). The API serves partial data
with `stale` metadata while the backfill runs.

## 4. Reverse proxy cutover

Point the site at `http://127.0.0.1:8330` (old app used 8321/8322). Keep the old container and
data volume until the new service has passed acceptance. Set `MI_HEALTH_HTTPS_ONLY=1` and restart
the new container so the session cookie is `Secure`.

MCP endpoint: `https://<host>/mcp/` with `Authorization: Bearer mhk_...`.
REST endpoint: `https://<host>/api/...`.

## 5. Rollback

1. Restore the reverse-proxy upstream to the old app (8321/8322).
2. Leave the old container and volume in place.
3. The new service's data is independent (`mi-health-data`); no dual-write.

## 6. Acceptance checks

- `GET /healthz` returns 200 on loopback.
- Admin login sets an HttpOnly session; `/api/keys` works only with that session.
- An agent key can query 90 days over REST and MCP; responses include `data_as_of`,
  `last_synced_at`, `stale`.
- During backfill, API responses remain fast and mark stale data.
- Public access only through the TLS proxy; `127.0.0.1:8330` is not exposed directly.
