from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]


def _uncovered_test_dirs(testpaths: list[str], repo_root: Path) -> list[str]:
    return [
        test_dir.relative_to(repo_root).as_posix()
        for test_dir in repo_root.glob("src/django_core_micha/*/tests")
        if test_dir.is_dir()
        and test_dir.relative_to(repo_root).as_posix() not in testpaths
    ]


def test_pytest_testpaths_cover_all_subpackage_test_directories() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        testpaths = tomllib.load(pyproject_file)["tool"]["pytest"]["ini_options"][
            "testpaths"
        ]

    uncovered = _uncovered_test_dirs(testpaths, REPO_ROOT)

    assert not uncovered, f"Subpackage test directories absent from pytest testpaths: {uncovered}"


def test_coverage_guard_detects_the_previous_messaging_omission() -> None:
    stale_testpaths = [
        "tests",
        "src/django_core_micha/notifications/tests",
        "src/django_core_micha/onboarding/tests",
    ]

    uncovered = _uncovered_test_dirs(stale_testpaths, REPO_ROOT)

    assert "src/django_core_micha/messaging/tests" in uncovered
