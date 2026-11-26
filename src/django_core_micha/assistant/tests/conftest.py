"""Assistant database fixtures, including migration-created lifecycle state."""
import pytest


@pytest.fixture(autouse=True)
def catalogue_state_for_database_tests(request):
    # Transactional tests flush migration data. Restore only the initial test
    # fixture, after pytest-django has prepared the database for this test.
    # Do not give pure unit tests database access.
    if not (request.node.get_closest_marker("django_db") or
            {"db", "transactional_db", "live_server"}.intersection(request.fixturenames)):
        return
    request.getfixturevalue("db")
    from django_core_micha.assistant.models import AssistantCatalogueState

    AssistantCatalogueState.objects.get_or_create(pk=1)


@pytest.fixture(autouse=True)
def app_authorization_snapshot_stub(settings):
    # Real apps must replace this with a transactionally locked durable snapshot.
    settings.DCM_ASSISTANT_LOCK_AUTHORIZATION_SNAPSHOT = lambda user_id: {
        "current": {"global": 0, "user": 0},
        "baseline": {"global": 0, "user": 0},
    }
