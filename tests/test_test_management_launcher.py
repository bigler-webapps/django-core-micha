import io

import pytest

from django_core_micha.scripts import isolated_test_start


def test_launcher_reports_only_fixed_diagnostic_without_process_output(monkeypatch, tmp_path, capsys):
    class FailedProcess:
        stdout = io.StringIO("synthetic-private-value permission denied\n")
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}) == 1
    output = capsys.readouterr().out
    assert "Docker denied access" in output
    assert "synthetic-private-value" not in output


@pytest.mark.parametrize(("failure", "message"), [
    ("required variable FAKE_SECRET is missing a value: fake-token", "Compose requires a missing or empty configuration value."),
    ("invalid interpolation format for services.backend: fake-token", "Compose rejected a configuration interpolation."),
    ("services.backend Additional properties are not allowed (FAKE_SECRET was unexpected)", "Compose rejected an unsupported configuration property."),
    ("service backend refers to undefined service fake-service: invalid compose project", "Compose references an undefined service."),
    ("unable to prepare context: path /private/fake-token not found", "Compose cannot access the image build context."),
    ("Cannot connect to the Docker daemon at unix:///private/fake-token", "Docker daemon is unavailable."),
])
def test_compose_failures_report_fixed_categories(monkeypatch, tmp_path, capsys, failure, message):
    class FailedProcess:
        stdout = io.StringIO(failure)
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}) == 1
    assert capsys.readouterr().out == f"[WARN] {message}\n"


@pytest.mark.parametrize("failure", [
    "FAKE_SECRET=fake-token /private/path user:fake-password@host",
    "required variable FAKE_SECRET was mentioned in an unrelated error",
])
def test_unknown_hostile_output_is_not_printed(monkeypatch, tmp_path, capsys, failure):
    class FailedProcess:
        stdout = io.StringIO(failure)
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}) == 1
    assert capsys.readouterr().out == ""


def test_compose_failure_split_across_chunks_is_redacted(monkeypatch, tmp_path, capsys):
    class ChunkedOutput:
        chunks = iter(["FAKE_SECRET=fake-token required vari", "able FAKE_SECRET is missing a value /private/path", ""])

        def read(self, size):
            return next(self.chunks)

    class FailedProcess:
        stdout = ChunkedOutput()
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}) == 1
    assert capsys.readouterr().out == "[WARN] Compose requires a missing or empty configuration value.\n"


@pytest.mark.parametrize(("failure", "message"), [
    ("django.db.migrations.exceptions.InconsistentMigrationHistory: detail", "Migration history is inconsistent."),
    ("django.db.migrations.exceptions.NodeNotFoundError: detail", "A migration dependency is missing."),
    ("django.db.migrations.exceptions.CircularDependencyError: detail", "Migration dependencies contain a cycle."),
    ("django.db.utils.OperationalError: detail", "The migration encountered a database operational error."),
    ("django.db.utils.IntegrityError: detail", "The migration encountered a database integrity error."),
    ("django.db.utils.ProgrammingError: detail", "The migration encountered a database schema error."),
    ("CommandError: Conflicting migrations detected; detail", "Migration branches conflict."),
])
def test_migration_fixed_categories_are_redacted(monkeypatch, tmp_path, capsys, failure, message):
    class FailedProcess:
        stdout = io.StringIO("FAKE_SECRET=fake-token /private/path user:fake-password@host\n" + failure)
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 1
    assert capsys.readouterr().out == f"[WARN] {message}\n"


@pytest.mark.parametrize("migration, expected", [(True, "[WARN] A migration dependency is missing.\n"), (False, "")])
def test_migration_category_split_across_chunks_is_scoped(monkeypatch, tmp_path, capsys, migration, expected):
    class ChunkedOutput:
        chunks = iter(["FAKE_SECRET=fake-token /private/path\ndjango.db.migrations.exceptions.Node", "NotFoundError: user:fake-password@host", ""])

        def read(self, size):
            return next(self.chunks)

    class FailedProcess:
        stdout = ChunkedOutput()
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=migration) == 1
    assert capsys.readouterr().out == expected


def test_unknown_migration_failure_stays_generic(monkeypatch, tmp_path, capsys):
    class FailedProcess:
        stdout = io.StringIO("FAKE_SECRET=fake-token /private/path user:fake-password@host\nUnknownError: detail")
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(("failure", "message"), [
    ("ModuleNotFoundError: No module named 'fake'", "A Python module could not be imported during migration."),
    ("ImportError: cannot import name 'fake'", "A Python module could not be imported during migration."),
    ("django.core.exceptions.ImproperlyConfigured: detail", "Django configuration prevented migration startup."),
    ("django.core.exceptions.AppRegistryNotReady: detail", "The Django app registry was not ready during migration startup."),
    ("SystemCheckError: System check identified some issues:", "Django system checks failed during migration startup."),
    ("django.core.management.base.SystemCheckError: detail", "Django system checks failed during migration startup."),
    ("SyntaxError: invalid syntax", "A Python syntax error prevented migration startup."),
    ("IndentationError: unexpected indent", "A Python syntax error prevented migration startup."),
    ("TabError: inconsistent indentation", "A Python syntax error prevented migration startup."),
])
def test_migration_startup_categories_are_fixed_and_redacted(monkeypatch, tmp_path, capsys, failure, message):
    class FailedProcess:
        stdout = io.StringIO("FAKE_SECRET=fake-token /private/path user:fake-password@host\n" + failure)
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 1
    assert capsys.readouterr().out == f"[WARN] {message}\n"


@pytest.mark.parametrize("migration", [True, False])
def test_migration_startup_category_split_and_scoped(monkeypatch, tmp_path, capsys, migration):
    class ChunkedOutput:
        chunks = iter(["FAKE_SECRET=fake-token /private/path\nModuleNot", "FoundError: user:fake-password@host", ""])

        def read(self, size):
            return next(self.chunks)

    class FailedProcess:
        stdout = ChunkedOutput()
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=migration) == 1
    expected = "[WARN] A Python module could not be imported during migration.\n" if migration else ""
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize("failure", [
    "prefix ModuleNotFoundError: detail",
    "UnknownSyntaxError: detail",
    "django.core.exceptions.ImproperlyConfiguredExtra: detail",
    "SystemCheckErrorWithoutColon detail",
])
def test_migration_startup_near_matches_remain_generic(monkeypatch, tmp_path, capsys, failure):
    class FailedProcess:
        stdout = io.StringIO("FAKE_SECRET=fake-token /private/path user:fake-password@host\n" + failure)
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("summary_first", [True, False])
@pytest.mark.parametrize("migration", [True, False])
def test_system_check_categories_accumulate_across_chunks(monkeypatch, tmp_path, capsys, summary_first, migration):
    summary = "SystemCheckError: System check identified some issues:\n"
    checks = (
        "FAKE_SECRET=fake-token /private/path user:fake-password@host: (fields.E001) hidden\n"
        "private.name: (models.W002) hidden\n"
        "private.name: (admin.E003) hidden\n"
        "private.name: (urls.E004) hidden\n"
        "private.name: (security.W005) hidden\n"
        "private.name: (staticfiles.E006) hidden\n"
        "private.name: (invented.E007) hidden\n"
    )
    output = summary + checks if summary_first else checks + summary

    class ChunkedOutput:
        chunks = iter([output[i:i + 7] for i in range(0, len(output), 7)] + [""])

        def read(self, size):
            return next(self.chunks)

    class FailedProcess:
        stdout = ChunkedOutput()
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=migration) == 1
    expected = (
        "[WARN] Django system checks failed during migration startup.\n"
        "[WARN] Django system check categories: admin, fields, models, security, staticfiles, unknown, urls.\n"
    ) if migration else ""
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize("output, expected", [
    ("SystemCheckError: hidden\nprivate: (custom.E123) hidden", "[WARN] Django system checks failed during migration startup.\n[WARN] Django system check categories: unknown.\n"),
    ("private: (fields.E001) hidden", ""),
    ("SystemCheckError: hidden\nprivate: (fields.E001extra) hidden\nprivate: (fields.X001) hidden", "[WARN] Django system checks failed during migration startup.\n"),
])
def test_system_check_unknown_and_near_matches(monkeypatch, tmp_path, capsys, output, expected):
    class FailedProcess:
        stdout = io.StringIO("FAKE_SECRET=fake-token /private/path user:fake-password@host\n" + output)
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 1
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize("prefix, expected", [
    ("x", ""),
    ("\n", "[WARN] A Python module could not be imported during migration.\n"),
])
def test_migration_tail_preserves_real_line_boundary(monkeypatch, tmp_path, capsys, prefix, expected):
    class ChunkedOutput:
        chunks = iter(["x" * 256 + prefix + "ModuleNotFoundError:" + "x" * 236, " FAKE_SECRET=fake-token", ""])

        def read(self, size):
            return next(self.chunks)

    class FailedProcess:
        stdout = ChunkedOutput()
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 1
    assert capsys.readouterr().out == expected


def test_successful_process_does_not_print_diagnostic_or_private_output(monkeypatch, tmp_path, capsys):
    class SuccessfulProcess:
        stdout = io.StringIO("ModuleNotFoundError: FAKE_SECRET=fake-token /private/path user:fake-password@host\npermission denied")
        returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: SuccessfulProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}, migration=True) == 0
    assert capsys.readouterr().out == ""


def test_finish_start_enables_migration_diagnostics(monkeypatch, tmp_path):
    calls = []
    env = {}
    compose = ["docker", "compose"]
    monkeypatch.setattr(isolated_test_start, "_load_consumer", lambda *args, **kwargs: (tmp_path, "sample", False, "postgresql"))
    monkeypatch.setattr(isolated_test_start, "validate_runtime", lambda *args, **kwargs: (None, "127.0.0.1"))
    monkeypatch.setattr(isolated_test_start, "_docker_json", lambda *args, **kwargs: "postgres:18")

    def run_private(command, **kwargs):
        calls.append((command, kwargs))
        return int(command[-2:] == ["migrate", "--noinput"])

    monkeypatch.setattr(isolated_test_start, "_run_private", run_private)
    with pytest.raises(isolated_test_start.IsolationError, match="isolated database migration failed"):
        isolated_test_start._finish_start("project", None, compose, tmp_path, env, identity="sample")
    command, kwargs = calls[-1]
    assert command == compose + ["exec", "-T", "backend", "python", "manage.py", "migrate", "--noinput"]
    assert kwargs["migration"] is True
