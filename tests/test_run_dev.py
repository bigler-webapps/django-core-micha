"""Tests for run-dev's celery auto-detection, the --java opt-in flag, and DCM-DX-5's
UV_FLAGS/pnpm-drift/readiness/traefik-ownership pure functions."""
import yaml

from django_core_micha.scripts.run_dev import (
    compute_active_local_profiles,
    load_compose_service_names,
    OPTIONAL_LOCAL_SERVICE_PROFILES,
    resolve_build_and_uv_flags,
    frontend_needs_pnpm_install,
    wait_for_backend_ready,
    format_ready_line,
    format_timeout_line,
    parse_compose_port_output,
    should_remove_traefik_container,
    compute_expected_compose_project_name,
    load_project_yaml_name,
)


def _write_compose(path, services):
    path.write_text(yaml.safe_dump({"services": {name: {} for name in services}}), encoding="utf-8")


def test_load_compose_service_names_merges_across_files(tmp_path):
    base = tmp_path / "docker-compose.yml"
    local = tmp_path / "docker-compose.local.yml"
    _write_compose(base, ["backend", "db", "celery_worker", "java_backend"])
    _write_compose(local, ["celery_worker", "java_backend"])  # overlay redeclares, adds nothing new

    names = load_compose_service_names(["-f", str(base), "-f", str(local)])

    assert names == {"backend", "db", "celery_worker", "java_backend"}


def test_load_compose_service_names_ignores_missing_files(tmp_path):
    missing = tmp_path / "does-not-exist.yml"
    names = load_compose_service_names(["-f", str(missing)])
    assert names == set()


def test_celery_auto_detected_from_compose_without_the_flag():
    state = compute_active_local_profiles(
        celery_flag=False,
        java_flag=False,
        local_profiles_eligible=True,
        compose_service_names={"backend", "celery_worker"},
    )
    assert state == {"celery": True, "java": False}


def test_celery_flag_still_forces_it_even_if_not_defined():
    state = compute_active_local_profiles(
        celery_flag=True,
        java_flag=False,
        local_profiles_eligible=True,
        compose_service_names={"backend"},
    )
    assert state["celery"] is True


def test_java_is_opt_in_only_never_auto_detected():
    # A project defining java_backend must NOT auto-enable it -- only --java does.
    state = compute_active_local_profiles(
        celery_flag=False,
        java_flag=False,
        local_profiles_eligible=True,
        compose_service_names={"backend", "java_backend"},
    )
    assert state["java"] is False

    state = compute_active_local_profiles(
        celery_flag=False,
        java_flag=True,
        local_profiles_eligible=True,
        compose_service_names={"backend"},
    )
    assert state["java"] is True


def test_no_local_profiles_outside_edge_or_spool_eligibility():
    # Mirrors edge/spool mode: optional services run unconditionally there, with no
    # profile gate at all, so run-dev must not report either as "active" via profiles.
    state = compute_active_local_profiles(
        celery_flag=True,
        java_flag=True,
        local_profiles_eligible=False,
        compose_service_names={"celery_worker", "java_backend"},
    )
    assert state == {"celery": False, "java": False}


def test_optional_local_service_profiles_covers_celery_and_java():
    assert set(OPTIONAL_LOCAL_SERVICE_PROFILES) == {"celery", "java"}
    assert OPTIONAL_LOCAL_SERVICE_PROFILES["celery"] == ["celery_worker", "celery_beat"]
    assert OPTIONAL_LOCAL_SERVICE_PROFILES["java"] == ["java_backend"]


# --- DCM-DX-5 ---------------------------------------------------------------


def test_build_alone_does_not_refresh_uv_deps():
    should_build, uv_flags = resolve_build_and_uv_flags(build=True, refresh_deps=False, refresh=False)
    assert should_build is True
    assert uv_flags == ""


def test_refresh_deps_forces_refresh_and_implies_build():
    should_build, uv_flags = resolve_build_and_uv_flags(build=False, refresh_deps=True, refresh=False)
    assert should_build is True
    assert uv_flags == "--refresh"


def test_deprecated_refresh_still_means_build_without_refreshing():
    # The deprecated alias must not be silently repurposed to mean --refresh-deps.
    should_build, uv_flags = resolve_build_and_uv_flags(build=False, refresh_deps=False, refresh=True)
    assert should_build is True
    assert uv_flags == ""


def test_pnpm_install_runs_when_node_modules_absent():
    assert frontend_needs_pnpm_install(node_modules_exists=False, marker_mtime=None, source_mtimes=[]) is True


def test_pnpm_install_runs_when_lockfile_newer_than_marker():
    assert frontend_needs_pnpm_install(node_modules_exists=True, marker_mtime=100.0, source_mtimes=[200.0]) is True


def test_pnpm_install_skipped_when_marker_newer_than_lockfile():
    assert frontend_needs_pnpm_install(node_modules_exists=True, marker_mtime=200.0, source_mtimes=[100.0]) is False


def test_readiness_reports_ready_line_on_success():
    result = wait_for_backend_ready(
        is_ready=lambda: True,
        url="http://localhost:8000/",
        timeout_seconds=5,
        poll_interval_seconds=1,
        sleep_fn=lambda _seconds: None,
        now_fn=lambda: 0.0,
    )
    assert result == format_ready_line("http://localhost:8000/")
    assert result == "READY http://localhost:8000/"


def test_readiness_reports_timeout_line_when_exhausted():
    clock = {"t": 0.0}

    def fake_now():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds

    result = wait_for_backend_ready(
        is_ready=lambda: False,
        url="http://localhost:8000/",
        timeout_seconds=5,
        poll_interval_seconds=1,
        sleep_fn=fake_sleep,
        now_fn=fake_now,
    )
    assert result == format_timeout_line(5)
    assert result == "TIMEOUT after 5s"


def test_parse_compose_port_output_extracts_port():
    assert parse_compose_port_output("0.0.0.0:32771\n") == "32771"


def test_parse_compose_port_output_handles_empty_output():
    assert parse_compose_port_output("") is None
    assert parse_compose_port_output("\n") is None


def test_traefik_removed_when_it_belongs_to_own_project():
    assert should_remove_traefik_container("myapp", "myapp") is True


def test_traefik_kept_when_it_belongs_to_a_foreign_project():
    assert should_remove_traefik_container("webapp-management", "myapp") is False


def test_traefik_no_op_when_no_container():
    assert should_remove_traefik_container(None, "myapp") is False


def test_expected_project_name_mirrors_generate_env_formula():
    # generate_env.py writes COMPOSE_PROJECT_NAME=f"{project_name}_{env_name}" into
    # .env (auto-loaded by docker-compose) -- the directory basename is not involved.
    name = compute_expected_compose_project_name(
        explicit_override=None, project_name_from_yaml="hram", env_mode="local", fallback_name="fallback",
    )
    assert name == "hram_local"


def test_expected_project_name_explicit_override_wins():
    name = compute_expected_compose_project_name(
        explicit_override="custom_name", project_name_from_yaml="hram", env_mode="local", fallback_name="fallback",
    )
    assert name == "custom_name"


def test_expected_project_name_falls_back_without_project_yaml():
    name = compute_expected_compose_project_name(
        explicit_override=None, project_name_from_yaml=None, env_mode="local", fallback_name="fallback",
    )
    assert name == "fallback"


def test_load_project_yaml_name_reads_project_name(tmp_path):
    (tmp_path / "project.yaml").write_text("project_name: hram\n", encoding="utf-8")
    assert load_project_yaml_name(tmp_path) == "hram"


def test_load_project_yaml_name_missing_file_returns_none(tmp_path):
    assert load_project_yaml_name(tmp_path) is None
