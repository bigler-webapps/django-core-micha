from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AdapterAccountResult:
    adapter_ref: str
    user_ref: str = ""
    effective: dict[str, Any] | None = None
    relationships: dict[str, Any] | None = None


class TestManagementAdapter(Protocol):
    def discovery(self) -> dict[str, Any]: ...

    def create_account(self, *, run: Any, account_handle: str, payload: dict[str, Any]) -> AdapterAccountResult: ...

    def inspect_account(self, *, run: Any, account: Any) -> dict[str, Any]: ...

    def update_account(self, *, run: Any, account: Any, payload: dict[str, Any]) -> dict[str, Any]: ...

    def delete_account(self, *, run: Any, account: Any) -> None: ...

    def prepare_fixture(self, *, run: Any, fixture_handle: str, name: str, parameters: dict[str, Any]) -> dict[str, Any]: ...

    def reset_run(self, *, run: Any) -> dict[str, Any]: ...

    def close_run(self, *, run: Any) -> dict[str, Any]: ...

    def recover_run(self, *, run: Any) -> dict[str, Any]: ...
