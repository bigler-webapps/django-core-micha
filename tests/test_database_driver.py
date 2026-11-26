import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
from django.conf import settings
from django.db import connection

from tests import conftest


def test_configured_database_driver_is_psycopg3():
    assert connection.vendor == "postgresql"
    assert connection.Database.__name__ == "psycopg"


def test_no_configured_database_uses_sqlite():
    assert all(
        db["ENGINE"] != "django.db.backends.sqlite3"
        for db in settings.DATABASES.values()
    )


def test_unavailable_postgres_stops_session_with_start_instruction():
    db = settings.DATABASES["default"]
    with patch("psycopg.connect", side_effect=psycopg.OperationalError("unavailable")) as connect:
        with pytest.raises(pytest.exit.Exception) as error:
            conftest.pytest_sessionstart(None)

    connect.assert_called_once_with(
        host=db["HOST"], port=db["PORT"], dbname=db["NAME"],
        user=db["USER"], password=db["PASSWORD"], connect_timeout=5,
    )
    assert error.value.returncode == 2
    assert "docker compose -f compose.test-db.yml up -d" in str(error.value)


def test_database_settings_honor_explicit_overrides():
    overrides = {
        "DCM_TEST_DB_HOST": "synthetic-db-host",
        "DCM_TEST_DB_PORT": "6543",
        "DCM_TEST_DB_NAME": "synthetic_database",
        "DCM_TEST_DB_USER": "synthetic_user",
        "DCM_TEST_DB_PASSWORD": "synthetic_password",
    }
    settings_path = Path(__file__).with_name("settings.py")
    result = subprocess.run(
        [sys.executable, "-c", "import json, runpy, sys; "
         "print(json.dumps(runpy.run_path(sys.argv[1])['DATABASES']['default']))",
         str(settings_path)],
        env=overrides, capture_output=True, text=True, check=True,
    )
    db = json.loads(result.stdout)
    assert {key: db[key] for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")} == {
        "HOST": overrides["DCM_TEST_DB_HOST"],
        "PORT": overrides["DCM_TEST_DB_PORT"],
        "NAME": overrides["DCM_TEST_DB_NAME"],
        "USER": overrides["DCM_TEST_DB_USER"],
        "PASSWORD": overrides["DCM_TEST_DB_PASSWORD"],
    }
