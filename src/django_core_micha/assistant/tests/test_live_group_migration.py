import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("start", ["0009_stable_family_selection", "0012_catalogue_baseline"])
def test_live_group_grants_migration_preserves_choices(start):
    executor = MigrationExecutor(connection)
    executor.migrate([("dcm_assistant", start)])
    old_apps = executor.loader.project_state([("dcm_assistant", start)]).apps
    User = old_apps.get_model("auth", "User")
    Chat = old_apps.get_model("dcm_assistant", "AssistantChat")
    user = User.objects.create(username="migration-owner")
    null_chat = Chat.objects.create(owner_id=user.pk)
    saved_pk = None
    if start == "0012_catalogue_baseline":
        saved_pk = Chat.objects.create(owner_id=user.pk, group_grants={"read": {"member": "revision"}, "write": {}}).pk
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([("dcm_assistant", "0013_live_group_grants")])
        apps = executor.loader.project_state([("dcm_assistant", "0013_live_group_grants")]).apps
        MigratedChat = apps.get_model("dcm_assistant", "AssistantChat")
        assert MigratedChat.objects.get(pk=null_chat.pk).group_grants is None
        if saved_pk:
            assert set(MigratedChat.objects.get(pk=saved_pk).group_grants) == {"read", "write"}
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
        assert "dcm_assistant_assistantcataloguestate" not in tables
        assert "dcm_assistant_assistantcataloguedeployment" not in tables
    finally:
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
