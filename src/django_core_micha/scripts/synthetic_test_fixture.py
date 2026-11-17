"""Create and validate the fixed disposable consumer used by local launcher tests."""

import os
from pathlib import Path
import tempfile


IDENTITY = "dcm-synthetic"
PREFIX = "dcm-synthetic-"
FILES = {
    "test-management.consumer.json": '''{"version": 1, "identity": "dcm-synthetic", "mode": "synthetic-local"}
''',
    "docker-compose.test-management.yml": '''# Standalone configuration. Never merge this file with the ordinary local stack.
# run-dev must prove isolation before running migrations or enabling management.
services:
  backend:
    image: ${TEST_MANAGEMENT_IMAGE:?missing isolated image}
    build:
      context: .
      dockerfile: backend/Dockerfile
    command: ["python", "-m", "django_core_micha.test_management.isolated_entrypoint"]
    environment:
      DJANGO_SETTINGS_MODULE: backend.local_test_management_settings
      ENV_TYPE: local
      DB_HOST: db
      DB_PORT: "5432"
      DB_NAME: ${TEST_MANAGEMENT_DATABASE:?missing isolated database name}
      DB_USER: test_management
      DB_PASSWORD: ${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}
      DJANGO_SECRET_KEY: ${TEST_MANAGEMENT_DJANGO_SECRET:?missing isolated application secret}
      TEST_MANAGEMENT_INSTANCE_ID: ${TEST_MANAGEMENT_INSTANCE_ID:?missing instance ID}
      TEST_MANAGEMENT_CONTROLLER_TOKEN: ${TEST_MANAGEMENT_CONTROLLER_TOKEN:?missing controller token}
      FRONTEND_BUILD_DIR: /app/frontend_host/build_current
      PYTHONDONTWRITEBYTECODE: "1"
      PYTHONUNBUFFERED: "1"
    volumes:
      - ./backend:/app/backend:ro
      - ./frontend/build_current:/app/frontend_host/build_current:ro
      - type: volume
        source: media
        target: /app/backend/media
        volume:
          nocopy: true
    depends_on:
      db:
        condition: service_healthy
    networks:
      - isolated

  db:
    image: postgres:18
    environment:
      POSTGRES_DB: ${TEST_MANAGEMENT_DATABASE:?missing isolated database name}
      POSTGRES_USER: test_management
      POSTGRES_PASSWORD: ${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}
    volumes:
      - type: volume
        source: database
        target: /var/lib/postgresql
        volume:
          nocopy: true
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test_management -d $${POSTGRES_DB}"]
      interval: 2s
      timeout: 2s
      retries: 30
    networks:
      - isolated

networks:
  isolated:
    internal: true
    ipam:
      config:
        - subnet: ${TEST_MANAGEMENT_SUBNET:?missing isolated network subnet}

volumes:
  database: {}
  media: {}
''',
    "backend/Dockerfile": '''FROM python:3.13-slim
RUN python -m pip install --no-cache-dir Django==6.1.3 "psycopg[binary]==3.3.4" daphne==4.2.1
COPY shared-core /opt/shared-core
ENV PYTHONPATH=/opt/shared-core
WORKDIR /app/backend
''',
    "backend/manage.py": '''import os
import sys

from django.core.management import execute_from_command_line

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.local_test_management_settings")
execute_from_command_line(sys.argv)
''',
    "backend/backend/__init__.py": '''''',
    "backend/backend/urls.py": '''from django.http import JsonResponse
from django.urls import path

urlpatterns = [path("api/healthz", lambda request: JsonResponse({"status": "ok"}))]
''',
    "backend/backend/asgi.py": '''from django.core.asgi import get_asgi_application

application = get_asgi_application()
''',
    "backend/backend/local_test_management_settings.py": '''import os

from django_core_micha.test_management.isolation import verify_marker

if os.environ.get("ENV_TYPE", "").lower() != "local":
    raise RuntimeError("The synthetic test instance requires a local environment.")
verify_marker()
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
ROOT_URLCONF = "backend.urls"
ASGI_APPLICATION = "backend.asgi.application"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                  "django_core_micha.test_management"]
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "HOST": os.environ["DB_HOST"],
                         "PORT": os.environ["DB_PORT"], "NAME": os.environ["DB_NAME"],
                         "USER": os.environ["DB_USER"], "PASSWORD": os.environ["DB_PASSWORD"]}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ENV_TYPE = "local"
TEST_MANAGEMENT_ORIGINAL_ENV_TYPE = "local"
TEST_MANAGEMENT_ENABLED = True
TEST_MANAGEMENT_REMOTE_ACCESS = False
TEST_MANAGEMENT_INSTANCE_ID = os.environ["TEST_MANAGEMENT_INSTANCE_ID"]
TEST_MANAGEMENT_CONTROLLER_TOKEN = os.environ["TEST_MANAGEMENT_CONTROLLER_TOKEN"]
TEST_MANAGEMENT_CONTROLLER_TTL_SECONDS = 900
MEDIA_ROOT = "/app/backend/media"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = []
CORS_ALLOWED_ORIGIN_REGEXES = []
CORS_ALLOW_CREDENTIALS = False
''',
    "frontend/build_current/index.html": '''<!doctype html><html><body>Synthetic test instance</body></html>
''',
}
# Copy only this package's required, named source files. No application tree,
# environment file, installed app image or unrelated core component is included.
_CORE_ROOT = Path(__file__).resolve().parents[1]
_CORE_FILES = (
    "test_management/__init__.py", "test_management/apps.py", "test_management/models.py",
    "test_management/activation.py", "test_management/isolation.py",
    "test_management/isolated_entrypoint.py", "test_management/http_relay.py",
    "test_management/migrations/__init__.py", "test_management/migrations/0001_initial.py",
    "test_management/management/__init__.py", "test_management/management/commands/__init__.py",
    "test_management/management/commands/renew_test_controller.py",
)
FILES["shared-core/django_core_micha/__init__.py"] = ""
for _source in _CORE_FILES:
    FILES[f"shared-core/django_core_micha/{_source}"] = (_CORE_ROOT / _source).read_text(encoding="utf-8")
DIRECTORIES = {"backend/media"}
for _relative in FILES:
    DIRECTORIES.update(str(parent).replace("\\", "/") for parent in Path(_relative).parents if str(parent) != ".")


def create_fixture(parent=None):
    """Create a fresh fixture under the operating system's temporary directory."""
    temporary = Path(tempfile.gettempdir()).resolve()
    parent = temporary if parent is None else Path(parent).resolve(strict=True)
    if not parent.is_relative_to(temporary):
        raise ValueError("Synthetic fixtures require a temporary parent directory.")
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir=parent))
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    (root / "backend/media").mkdir()
    return root


def validate_fixture(root):
    """Reject copied declarations, altered source and pre-existing app trees."""
    root = Path(root).resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve()
    if not root.is_relative_to(temporary) or not root.name.startswith(PREFIX):
        raise ValueError("Synthetic mode requires a generated temporary fixture.")
    seen_files, seen_directories = set(), set()
    for current, directories, filenames in os.walk(root, followlinks=False):
        for name in directories + filenames:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Synthetic fixture contains a redirected path.")
            if path.is_dir():
                if relative not in DIRECTORIES:
                    raise ValueError("Synthetic fixture contains an unexpected directory.")
                seen_directories.add(relative)
            else:
                if relative not in FILES or not path.is_file():
                    raise ValueError("Synthetic fixture contains an unexpected file.")
                seen_files.add(relative)
    if seen_files != set(FILES) or seen_directories != DIRECTORIES:
        raise ValueError("Synthetic fixture layout differs from its generated contract.")
    for relative, content in FILES.items():
        if (root / relative).read_bytes() != content.encode("utf-8"):
            raise ValueError("Synthetic fixture source differs from its generated contract.")


def main():
    print(create_fixture())


if __name__ == "__main__":
    main()
