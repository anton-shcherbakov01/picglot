"""Operational CLI: `python -m picglot.cli <command>`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from picglot import __version__
from picglot.core.config import ConfigurationError, settings
from picglot.core.errors import AppError
from picglot.core.logging import configure_logging, get_logger

log = get_logger("cli")


def _ok(label: str, detail: str = "") -> str:
    return f"  [ok]   {label}{f' — {detail}' if detail else ''}"


def _warn(label: str, detail: str = "") -> str:
    return f"  [warn] {label}{f' — {detail}' if detail else ''}"


def _fail(label: str, detail: str = "") -> str:
    return f"  [FAIL] {label}{f' — {detail}' if detail else ''}"


def cmd_health(args: argparse.Namespace) -> int:
    """Check every dependency and report honestly what is and is not working."""
    from picglot.core.redis import redis_healthy
    from picglot.db.session import database_healthy
    from picglot.providers import email as email_provider
    from picglot.providers import ocr as ocr_providers
    from picglot.providers import translation as translation_providers
    from picglot.providers.llm import get_llm
    from picglot.services.storage import get_storage
    from picglot.vision.fonts import registry

    print(f"{settings.brand_name} {__version__} — {settings.environment}\n")
    failures = 0

    print("Infrastructure")
    if database_healthy():
        print(_ok("database"))
    else:
        print(_fail("database", "cannot connect — check DATABASE_URL"))
        failures += 1

    if redis_healthy():
        print(_ok("redis"))
    else:
        print(_warn("redis", "unavailable; using in-process fallback (single process only)"))

    try:
        if get_storage().healthy():
            print(_ok("object storage", settings.storage_backend))
        else:
            print(_fail("object storage", "unreachable — check S3 settings"))
            failures += 1
    except Exception as exc:
        print(_fail("object storage", type(exc).__name__))
        failures += 1

    print("\nOCR providers")
    any_ocr = False
    for report in ocr_providers.health_report():
        state = report["state"]
        line = _ok if state == "healthy" else (_warn if state != "unavailable" else _fail)
        print(line(report["name"], report["detail"] or state))
        any_ocr = any_ocr or state == "healthy"
    if not any_ocr:
        print(_fail("no OCR provider available", "install extras: pip install 'picglot[ocr]'"))
        failures += 1

    print("\nTranslation providers")
    any_translation = False
    for report in translation_providers.health_report():
        state = report["state"]
        line = _ok if state == "healthy" else _warn
        print(line(report["name"], report["detail"] or state))
        any_translation = any_translation or state == "healthy"
    if not any_translation:
        print(
            _warn(
                "no translation provider configured",
                "OCR and exports work; translation needs a provider key or offline models",
            )
        )

    print("\nOther")
    llm = get_llm().health()
    print((_ok if llm.state == "healthy" else _warn)("llm", llm.detail or llm.state))
    mail = email_provider.health()
    print((_ok if mail["state"] == "healthy" else _warn)("email", mail["detail"] or mail["state"]))

    coverage = registry.coverage_report()
    missing = [name for name, ok in coverage.items() if not ok]
    if missing:
        print(_warn("fonts", f"no glyph coverage for: {', '.join(missing)}"))
    else:
        print(_ok("fonts", f"{len(registry.files)} files, all scripts covered"))

    print()
    if failures:
        print(f"{failures} critical check(s) failed.")
        return 1
    print("All critical checks passed.")
    return 0


def cmd_wait_for_db(args: argparse.Namespace) -> int:
    from picglot.db.session import database_healthy, dispose_engine

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if database_healthy():
            print("database is ready")
            return 0
        dispose_engine()
        time.sleep(2)
    print(f"database not reachable within {args.timeout}s", file=sys.stderr)
    return 1


def cmd_seed(args: argparse.Namespace) -> int:
    from picglot.db.seed import run_seed

    result = run_seed(baseline_only=args.baseline_only, force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    from picglot.db.session import session_scope
    from picglot.domain.enums import AdminRole, UserStatus
    from picglot.services import auth as auth_service
    from picglot.services import credits as credit_service

    with session_scope() as session:
        user = auth_service.find_user(session, args.email)
        if user is None:
            user, _token = auth_service.register(
                session, email=args.email, password=args.password, name=args.name
            )
        user.admin_role = str(AdminRole(args.role))
        user.status = str(UserStatus.ACTIVE)
        from datetime import UTC, datetime

        user.email_verified_at = datetime.now(UTC)
        credit_service.get_or_create_wallet(session, user_id=user.id)
        print(f"admin ready: {user.email} ({user.admin_role})")
    return 0


def cmd_openapi(args: argparse.Namespace) -> int:
    from picglot.main import app

    document = app.openapi()
    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output} ({len(text)} bytes)")
    else:
        print(text)
    return 0


def cmd_lifecycle(args: argparse.Namespace) -> int:
    from picglot.db.session import session_scope
    from picglot.services import lifecycle

    with session_scope() as session:
        print(json.dumps(lifecycle.sweep(session), indent=2))
    return 0


def cmd_install_language_pack(args: argparse.Namespace) -> int:
    from picglot.providers.translation.local import install_language_pack

    print(json.dumps(install_language_pack(args.source, args.target), indent=2))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Print effective configuration with every secret masked."""
    from picglot.core.security import mask_secret

    secret_markers = ("key", "secret", "password", "token", "dsn", "credentials")
    data: dict[str, Any] = {}
    for name, value in settings.model_dump().items():
        if any(marker in name for marker in secret_markers) and isinstance(value, str):
            data[name] = mask_secret(value) if value else ""
        else:
            data[name] = value
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="picglot", description=f"{settings.brand_name} CLI")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="check dependencies and providers").set_defaults(func=cmd_health)

    wait = sub.add_parser("wait-for-db", help="block until the database accepts connections")
    wait.add_argument("--timeout", type=int, default=90)
    wait.set_defaults(func=cmd_wait_for_db)

    seed = sub.add_parser("seed", help="load plans, flags, SEO content and demo data")
    seed.add_argument(
        "--baseline-only",
        action="store_true",
        help="plans, flags and content only — no demo accounts",
    )
    seed.add_argument("--force", action="store_true", help="overwrite existing rows")
    seed.set_defaults(func=cmd_seed)

    admin = sub.add_parser("create-admin", help="create or promote an administrator")
    admin.add_argument("--email", required=True)
    admin.add_argument("--password", required=True)
    admin.add_argument("--name", default="Administrator")
    admin.add_argument("--role", default="superadmin")
    admin.set_defaults(func=cmd_create_admin)

    openapi = sub.add_parser("openapi", help="print or write the OpenAPI document")
    openapi.add_argument("--output", default=None)
    openapi.set_defaults(func=cmd_openapi)

    sub.add_parser("lifecycle", help="run the retention sweep once").set_defaults(
        func=cmd_lifecycle
    )

    pack = sub.add_parser("install-language-pack", help="install an offline Argos model")
    pack.add_argument("source")
    pack.add_argument("target")
    pack.set_defaults(func=cmd_install_language_pack)

    sub.add_parser("config", help="print effective configuration (secrets masked)").set_defaults(
        func=cmd_config
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except AppError as exc:
        # The message is deliberately generic — it is written for end users.
        # `details` carries the part an operator can act on (which field, which
        # rule), so print it instead of a traceback that hides it.
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        for key, value in (exc.details or {}).items():
            print(f"  {key}: {value}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
