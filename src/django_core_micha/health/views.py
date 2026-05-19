"""Health check view shared across django-core-micha consumer apps.

Probes runtime dependencies (database, cache) and returns a structured JSON
response. Used both by Uptime Kuma (60s liveness/dependency monitoring) and
by the staging-health PR gate before merging to main.

The endpoint is mounted at ``/api/healthz`` via :mod:`django_core_micha.api_urls`.

Response shape::

    {
      "status": "ok" | "degraded",
      "checks": {
        "db":    {"ok": bool, "duration_ms": float, "error"?: str},
        "cache": {"ok": bool, "duration_ms": float, "error"?: str}
      }
    }

HTTP status:
    200  all checks ok
    503  one or more checks failed (degraded)
"""
from __future__ import annotations

import time

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET


_ERROR_TRUNCATE = 200


def _check_db() -> dict:
    start = time.perf_counter()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — surface any failure mode
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": str(exc)[:_ERROR_TRUNCATE],
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
                "error": "round-trip mismatch (set value not readable)",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": str(exc)[:_ERROR_TRUNCATE],
        }
    return {
        "ok": True,
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }


@csrf_exempt
@never_cache
@require_GET
def healthz_view(request):
    """Return aggregated health status for the running app."""
    checks = {
        "db": _check_db(),
        "cache": _check_cache(),
    }
    overall_ok = all(c["ok"] for c in checks.values())
    return JsonResponse(
        {
            "status": "ok" if overall_ok else "degraded",
            "checks": checks,
        },
        status=200 if overall_ok else 503,
    )
