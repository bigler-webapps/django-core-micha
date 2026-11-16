from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction

from .activation import TestManagementError, authorize_request
from .serializers import AccountCreateSerializer, AccountUpdateSerializer, FixtureCreateSerializer, RunCreateSerializer
from .services import (
    account_payload, create_account, create_run, delete_account, discovery, get_account,
    prepare_fixture, _finish_run, update_account,
)
from .models import TestAccount, TestFixture


def _error(exc):
    if isinstance(exc, TestManagementError):
        return Response({"code": exc.code, "detail": exc.detail}, status=exc.status)
    return Response({"code": "internal_error", "detail": "The test-management operation failed."}, status=500)


class ManagementView(APIView):
    authentication_classes = []
    permission_classes = []
    # Authorization happens before DRF's initial checks and is restricted to
    # the isolated controller; ordinary application throttles still apply.
    throttle_classes = []

    def dispatch(self, request, *args, **kwargs):
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.args = args
        self.kwargs = kwargs
        self.headers = self.default_response_headers
        try:
            request.test_management_instance = authorize_request(request)
        except Exception as exc:
            response = _error(exc)
        else:
            try:
                self.initial(request, *args, **kwargs)
                handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
                response = handler(request, *args, **kwargs)
            except Exception as exc:
                response = self.handle_exception(exc)
        return self.finalize_response(request, response, *args, **kwargs)


class DiscoveryView(ManagementView):
    def get(self, request):
        try:
            return Response(discovery())
        except Exception as exc:
            return _error(exc)


class RunCollectionView(ManagementView):
    def post(self, request):
        serializer = RunCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "invalid_payload", "detail": serializer.errors}, status=400)
        try:
            run = create_run(serializer.validated_data)
            return Response({"handle": str(run.handle), "status": run.status, "baseline": run.baseline}, status=201 if getattr(run, "_created_now", False) else 200)
        except Exception as exc:
            return _error(exc)


class RunView(ManagementView):
    def get(self, request, run):
        try:
            from .services import _run
            obj = _run(request.test_management_instance, run, allow_recoverable=True)
            return Response({"handle": str(obj.handle), "status": obj.status, "baseline": obj.baseline})
        except Exception as exc:
            return _error(exc)


class RunActionView(ManagementView):
    operation = ""

    def post(self, request, run):
        try:
            obj = _finish_run(request.test_management_instance, run, self.operation)
            return Response({"handle": str(obj.handle), "status": obj.status, "cleanup": obj.adapter_state.get("last_cleanup", {})})
        except Exception as exc:
            return _error(exc)


class RecoverView(RunActionView):
    operation = "recover"

    def post(self, request, run):
        try:
            from .services import _run, adapter
            with transaction.atomic():
                obj = _run(request.test_management_instance, run, allow_recoverable=True, lock=True)
                if obj.status != obj.Status.RECOVERABLE or obj.recovery_reason != "restart":
                    raise TestManagementError("recovery_not_available", "Only an interrupted run can be recovered; failed cleanup must be retried.", 409)
                state = adapter().recover_run(run=obj)
                obj.status = obj.Status.ACTIVE
                obj.recovery_reason = ""
                obj.adapter_state = {**(obj.adapter_state or {}), "recovery": state or {}}
                obj.save(update_fields=["status", "recovery_reason", "adapter_state", "updated_at"])
            return Response({"handle": str(obj.handle), "status": obj.status})
        except Exception as exc:
            return _error(exc)


class AccountCollectionView(ManagementView):
    def get(self, request, run):
        try:
            from .services import _run
            obj = _run(request.test_management_instance, run)
            return Response([account_payload(account) for account in obj.accounts.filter(status=TestAccount.Status.ACTIVE)])
        except Exception as exc:
            return _error(exc)

    def post(self, request, run):
        serializer = AccountCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "invalid_payload", "detail": serializer.errors}, status=400)
        try:
            account = create_account(request.test_management_instance, run, serializer.validated_data)
            return Response(account_payload(account), status=201)
        except Exception as exc:
            return _error(exc)


class AccountView(ManagementView):
    def get(self, request, run, account):
        try:
            return Response(account_payload(get_account(request.test_management_instance, run, account)))
        except Exception as exc:
            return _error(exc)

    def patch(self, request, run, account):
        serializer = AccountUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "invalid_payload", "detail": serializer.errors}, status=400)
        try:
            return Response(account_payload(update_account(request.test_management_instance, run, account, serializer.validated_data)))
        except Exception as exc:
            return _error(exc)

    def delete(self, request, run, account):
        try:
            delete_account(request.test_management_instance, run, account)
            return Response(status=204)
        except Exception as exc:
            return _error(exc)


class FixtureView(ManagementView):
    def post(self, request, run):
        serializer = FixtureCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "invalid_payload", "detail": serializer.errors}, status=400)
        try:
            fixture = prepare_fixture(request.test_management_instance, run, serializer.validated_data)
            return Response({"handle": str(fixture.handle), "name": fixture.name, "state": fixture.state}, status=201)
        except Exception as exc:
            return _error(exc)
