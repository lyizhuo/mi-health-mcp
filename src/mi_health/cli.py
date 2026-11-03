"""Command line entry points."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys

from mi_health.api.app import create_app
from mi_health.api.auth import generate_key, normalize_scopes
from mi_health.config import Settings
from mi_health.credentials import CredentialStore
from mi_health.db import Database
from mi_health.mcp_server import create_mcp
from mi_health.services import QueryService
from mi_health.sync.service import DATA_TYPES, SyncService
from mi_health.sync.worker import Worker


def _context() -> tuple[Settings, Database, CredentialStore]:
    settings = Settings.from_env()
    settings.ensure_data_dir()
    db = Database(settings.db_path)
    store = CredentialStore(db, settings.master_key, settings.region)
    return settings, db, store


def cmd_set_credentials(args) -> int:
    settings, db, store = _context()
    store.set(args.user_id, args.pass_token, args.region)
    print("credentials stored encrypted")
    return 0


def cmd_clear_credentials(args) -> int:
    settings, db, store = _context()
    store.clear()
    print("credentials cleared")
    return 0


def cmd_key_create(args) -> int:
    settings, db, store = _context()
    token, hashed, prefix = generate_key()
    scopes = normalize_scopes(args.scopes)
    db.create_api_key(hashed, prefix, args.label, ",".join(sorted(scopes)))
    print(token)
    print(f"prefix={prefix} scopes={','.join(sorted(scopes))}", file=sys.stderr)
    return 0


def cmd_key_list(args) -> int:
    settings, db, store = _context()
    print(json.dumps(db.list_api_keys(), indent=2, ensure_ascii=False))
    return 0


def cmd_key_revoke(args) -> int:
    settings, db, store = _context()
    ok = db.revoke_api_key(args.prefix)
    print("revoked" if ok else "not found")
    return 0 if ok else 1


def cmd_sync(args) -> int:
    settings, db, store = _context()
    service = SyncService(db, store, settings)
    result = service.run(args.type, args.start_date, args.end_date, args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "ok" else 1


def cmd_admin_hash(args) -> int:
    from argon2 import PasswordHasher

    password = getpass.getpass("Admin password: ")
    if password != getpass.getpass("Confirm: "):
        print("passwords do not match", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(password))
    return 0


def cmd_status(args) -> int:
    settings, db, store = _context()
    service = QueryService(db, store, settings)
    try:
        print(json.dumps(service.status(), indent=2, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(f"status error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_serve_api(args) -> int:
    import uvicorn

    settings, db, store = _context()
    service = QueryService(db, store, settings)
    mcp = create_mcp(service, settings.allowed_hosts)
    app = create_app(settings, db, store, mcp)
    uvicorn.run(app, host=args.host or settings.host, port=args.port or settings.port, access_log=False)
    return 0


def cmd_serve_worker(args) -> int:
    settings, db, store = _context()
    Worker(db, store, settings).run_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mi-health", description="Xiaomi health data hub for agents")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("set-credentials", help="store encrypted Xiaomi userId/passToken")
    p.add_argument("--user-id", required=True)
    p.add_argument("--pass-token", required=True)
    p.add_argument("--region", default=None)
    p.set_defaults(func=cmd_set_credentials)

    p = sub.add_parser("clear-credentials", help="remove stored Xiaomi credentials")
    p.set_defaults(func=cmd_clear_credentials)

    keys = sub.add_parser("key", help="manage agent API keys")
    key_sub = keys.add_subparsers(dest="key_command", required=True)
    p = key_sub.add_parser("create")
    p.add_argument("--label", default=None)
    p.add_argument("--scopes", default="read", help="comma separated: read,sync")
    p.set_defaults(func=cmd_key_create)
    p = key_sub.add_parser("list")
    p.set_defaults(func=cmd_key_list)
    p = key_sub.add_parser("revoke")
    p.add_argument("--prefix", required=True)
    p.set_defaults(func=cmd_key_revoke)

    p = sub.add_parser("sync", help="sync one data type")
    p.add_argument("--type", required=True, choices=DATA_TYPES)
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("status", help="print local sync status")
    p.set_defaults(func=cmd_status)

    admin = sub.add_parser("admin", help="admin helpers")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    p = admin_sub.add_parser("hash-password", help="print an Argon2id hash for MI_HEALTH_ADMIN_PASSWORD_HASH")
    p.set_defaults(func=cmd_admin_hash)

    p = sub.add_parser("serve-api", help="run REST + MCP server")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve_api)

    p = sub.add_parser("serve-worker", help="run scheduler/sync worker")
    p.set_defaults(func=cmd_serve_worker)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

