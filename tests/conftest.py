import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")


def pytest_addoption(parser):
    parser.addoption("--dcm-disposal-proof-handles", default=None,
                     help="JSON file with two approved disposable instance IDs; enables the Docker proof")


def pytest_sessionstart(session):
    import psycopg
    import pytest
    from django.conf import settings

    db = settings.DATABASES["default"]
    try:
        with psycopg.connect(
            host=db["HOST"], port=db["PORT"], dbname=db["NAME"],
            user=db["USER"], password=db["PASSWORD"], connect_timeout=5,
        ):
            pass
    except psycopg.OperationalError:
        pytest.exit(
            "Test PostgreSQL is unreachable. Start it from the repository root: "
            "docker compose -f compose.test-db.yml up -d",
            returncode=2,
        )
