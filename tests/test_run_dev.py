"""Tests for run-dev's celery auto-detection and the new --java opt-in flag."""
import yaml

from django_core_micha.scripts.run_dev import (
    compute_active_local_profiles,
    load_compose_service_names,
    OPTIONAL_LOCAL_SERVICE_PROFILES,
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
