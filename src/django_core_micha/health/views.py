"""Health check view shared across django-core-micha consumer apps.

Probes runtime dependencies (database, cache) and returns a structured JSON
response. Used both by Uptime Kuma (60s liveness/dependency monitoring) and
by the staging-health PR gate before merging to main.

The endpoint is mounted at ``/api/healthz`` via :mod:`django_core_micha.api_urls`.

Response shape::

    {
      "status": "ok" | "degraded",
      "version": "<APP_GIT_SHA>" | null,
      "checks": {
        "db":         {"ok": bool, "duration_ms": float, "error"?: str},
        "cache":      {"ok": bool, "duration_ms": float, "error"?: str},
        "migrations": {"ok": bool, "duration_ms": float, "error"?: str},
        "config":     {"ok": bool, "duration_ms": float, "missing"?: [str], "error"?: str}
      }
    }

HTTP status:
    200  all checks ok
    503  one or more checks failed (degraded)

``version`` is an info field (never causes 503).  It is read from the
``APP_GIT_SHA`` environment variable and ``null`` when absent.

``migrations`` fails when ``MigrationExecutor`` finds an unapplied migration
plan — catches "DB up but schema stale" (e.g. cockpit schemaless boot).

``config`` fails when a critical configuration key is absent for the active
provider.  Only key *names* appear in ``missing``; values are never serialised.
Which keys are required is derived from the live settings, not hard-coded, so
apps without Resend or social login never get a false-503.
"""
from __future__ import annotations

import logging
import os
import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET


logger = logging.getLogger(__name__)


def _check_db() -> dict:
    start = time.perf_counter()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception:  # noqa: BLE001 — surface any failure mode
        # Do not leak driver-level error text (can include credentials, host
        # names, schema details). Log the full exception server-side instead.
        logger.exception("healthz: database check failed")
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "database check failed",
        }
    return {
        "ok": True,
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }


def _check_cache() -> dict:
    start = time.perf_counter()
    probe_key = "_djcm_health_probe"
    probe_value = str(int(time.time() * 1000))
    try:
        cache.set(probe_key, probe_value, timeout=5)
        retrieved = cache.get(probe_key)
        if retrieved != probe_value:
            return {
                "ok": False,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "error": "cache round-trip mismatch",
            }
    except Exception:  # noqa: BLE001
        logger.exception("healthz: cache check failed")
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "cache check failed",
        }
    return {
        "ok": True,
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }


def _check_migrations() -> dict:
    """Read-only migration plan check — no call_command('migrate')."""
    start = time.perf_counter()
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:  # noqa: BLE001
        logger.exception("healthz: migrations check failed")
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "migrations check failed",
        }
    if plan:
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": f"{len(plan)} migration(s) pending",
        }
    return {
        "ok": True,
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }


def _check_config() -> dict:
    """Check presence of critical config keys — names only, never values."""
    start = time.perf_counter()
    missing: list[str] = []
    try:
        # EMAIL_PROVIDER=resend requires RESEND_API_KEY (set via ANYMAIL by settings_base).
        # Apps not using Resend skip this check entirely — no false-503.
        email_provider = getattr(settings, "EMAIL_PROVIDER", "").lower().strip()
        if email_provider == "resend":
            resend_key = getattr(settings, "ANYMAIL", {}).get("RESEND_API_KEY", "")
            if not resend_key:
                missing.append("RESEND_API_KEY")

        # Social providers: client_id required per active provider when social_login is on.
        # AUTH_METHODS is per-app opt-in (settings_base default: social_login=True).
        auth_methods = getattr(settings, "AUTH_METHODS", {})
        if auth_methods.get("social_login"):
            social_providers = auth_methods.get("social_providers", [])
            socialaccount_providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})
            for provider in social_providers:
                client_id = (
                    socialaccount_providers
                    .get(provider, {})
                    .get("APP", {})
                    .get("client_id", "")
                )
                if not client_id:
                    missing.append(f"{provider.upper()}_CLIENT_ID")
    except Exception:  # noqa: BLE001
        logger.exception("healthz: config check failed")
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "config check failed",
        }

    result: dict = {
        "ok": not missing,
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }
    if missing:
        result["missing"] = missing
    return result


def _get_version_info() -> str | None:
    """Read APP_GIT_SHA from env — info only, never causes 503."""
    return os.environ.get("APP_GIT_SHA") or None


@csrf_exempt
@never_cache
@require_GET
def healthz_view(request):
    """Return aggregated health status for the running app."""
    checks = {
        "db": _check_db(),
        "cache": _check_cache(),
        "migrations": _check_migrations(),
        "config": _check_config(),
    }
    overall_ok = all(c["ok"] for c in checks.values())
    return JsonResponse(
        {
            "status": "ok" if overall_ok else "degraded",
            "version": _get_version_info(),
            "checks": checks,
        },
        status=200 if overall_ok else 503,
    )
