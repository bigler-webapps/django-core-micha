"""Run the canonical ui-core-micha service worker against a live DCM server.

The chain is real end to end: DCM issues the push payload, the worker script shows it,
and the worker's done click goes over HTTP with a session cookie and enforced CSRF.
Skipped when Node or the sibling ui-core-micha checkout is not available.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from django_core_micha.notifications import delivery, dispatch
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import NotificationPushActionToken, NotificationRecipient, PushSubscription
from django_core_micha.notifications.text_registry import _REGISTRY as TEXT_REGISTRY, register_notification_text
from django_core_micha.notifications.types import NotificationType, _REGISTRY as TYPE_REGISTRY, register_notification_type

SERVICE_WORKER = Path(os.environ.get(
    "UCM_SW_PATH",
    Path(__file__).resolve().parents[5] / "ui-core-micha" / "src" / "notifications" / "serviceWorker" / "sw.js",
))
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    not SERVICE_WORKER.exists() or NODE is None,
    reason="needs Node and the sibling ui-core-micha service worker",
)

RUNNER = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const jar = new Map(Object.entries(input.cookies));
const log = [];
async function fetchImpl(url, options = {}) {
  const cookie = [...jar].map(([name, value]) => `${name}=${value}`).join('; ');
  const headers = { ...(options.headers || {}), Origin: input.base };
  if (cookie) headers.Cookie = cookie;
  const response = await fetch(new URL(url, input.base), { ...options, headers });
  for (const line of response.headers.getSetCookie()) {
    const pair = line.split(';')[0];
    const index = pair.indexOf('=');
    jar.set(pair.slice(0, index), pair.slice(index + 1));
  }
  log.push({ url, method: options.method, status: response.status });
  return response;
}
const listeners = {};
const shown = [];
const self = {
  location: { origin: input.base },
  registration: { showNotification: async (title, options) => { shown.push({ title, options }); } },
  addEventListener: (type, listener) => { listeners[type] = listener; },
};
let openWindowCalled = false;
const clients = { matchAll: async () => [], openWindow: async () => { openWindowCalled = true; } };
new Function('self', 'clients', 'fetch', input.source)(self, clients, fetchImpl);
async function fire(type, event) {
  let waited;
  listeners[type]({ ...event, waitUntil: (promise) => { waited = promise; } });
  if (waited) await waited;
}
(async () => {
  await fire('push', { data: { json: () => JSON.parse(input.payload), text: () => input.payload } });
  const options = shown[0].options;
  let closed = false;
  await fire('notificationclick', {
    action: 'done',
    notification: { close: () => { closed = true; }, data: options.data },
  });
  console.log(JSON.stringify({ closed, openWindowCalled, actions: options.actions, log }));
})().catch((error) => { console.error(String(error)); process.exit(1); });
"""


@pytest.fixture
def push_stack(settings):
    settings.ROOT_URLCONF = "django_core_micha.notifications.tests.urls_push_actions"
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.MIDDLEWARE = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ]
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
    settings.STATIC_URL = "/static/"
    return settings


class _LiveServer:
    """A Django server started after the test's settings are in place (pytest-django's is session-wide)."""

    def __enter__(self):
        from django.test.testcases import LiveServerThread, _StaticFilesHandler

        self.thread = LiveServerThread("localhost", _StaticFilesHandler, port=0)
        self.thread.daemon = True
        self.thread.start()
        self.thread.is_ready.wait(timeout=30)
        if self.thread.error:
            raise self.thread.error
        self.url = f"http://localhost:{self.thread.port}"
        return self

    def __exit__(self, *exc_info):
        self.thread.terminate()
        self.thread.join(timeout=30)


def session_cookies(account):
    client = Client()
    assert client.login(username=account.username, password="password")
    return {name: morsel.value for name, morsel in client.cookies.items()}


def run_worker(base, payload, cookies, tmp_path):
    runner = tmp_path / "runner.js"
    runner.write_text(RUNNER, encoding="utf-8")
    data = {"base": base, "payload": payload, "cookies": cookies, "source": SERVICE_WORKER.read_text(encoding="utf-8")}
    result = subprocess.run([NODE, str(runner)], input=json.dumps(data), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.django_db(transaction=True)
def test_worker_done_click_against_live_dcm(push_stack, monkeypatch, tmp_path):
    with _LiveServer() as live_server:
        push_stack.PUBLIC_ORIGIN = live_server.url
        _scenario(live_server, monkeypatch, tmp_path)


def _scenario(live_server, monkeypatch, tmp_path):
    types, texts = TYPE_REGISTRY.copy(), TEXT_REGISTRY.copy()
    TYPE_REGISTRY.clear()
    TEXT_REGISTRY.clear()
    try:
        register_notification_text("open", {"de": "Oeffnen", "en": "Open"})
        register_notification_text("done", {"de": "Erledigt", "en": "Done"})
        register_notification_type(NotificationType(
            key="worker", category="system", mode="event", resolution="user-done", active=True, critical=True,
            push_open_label_key="open", push_done_label_key="done",
        ))
        payloads = {}

        def capture(**kwargs):
            user = kwargs["users"][0]
            payloads[user.username] = delivery._push_payload(
                title=kwargs["title"], body=kwargs["body"], url=kwargs["url"], extras=kwargs.get("extras"),
            )

        monkeypatch.setattr(dispatch, "_send_push", capture)
        accounts = {}
        for name in ("owner", "stranger", "switched", "loggedout"):
            account = get_user_model().objects.create_user(username=name, email=f"{name}@example.test", password="password")
            accounts[name] = account
            PushSubscription.objects.create(user=account, endpoint=f"https://fcm.googleapis.com/fcm/send/{name}", p256dh="k", auth="a")
        for name in ("owner", "switched", "loggedout"):
            notify(
                type="worker", recipients=accounts[name], channels=["push"],
                content={"title_key": "Task", "body_key": "Body", "link": "/tasks/1/"},
                push_open_url="/tasks/1/?from=push",
            )
        assert set(payloads) == {"owner", "switched", "loggedout"}

        # 1. Same signed-in account: completes, worker closes, one status change.
        ok = run_worker(live_server.url, payloads["owner"], session_cookies(accounts["owner"]), tmp_path)
        assert [entry["status"] for entry in ok["log"]] == [200, 204]
        assert ok["closed"] is True
        assert ok["openWindowCalled"] is False
        assert [action["action"] for action in ok["actions"]] == ["open", "done"]
        assert NotificationRecipient.objects.get(user=accounts["owner"]).done_at is not None

        # 2. A different account is signed in: denied, nothing changes, the notification stays.
        switched = run_worker(live_server.url, payloads["switched"], session_cookies(accounts["stranger"]), tmp_path)
        assert switched["log"][-1]["status"] == 403
        assert switched["closed"] is False
        assert switched["openWindowCalled"] is False
        assert NotificationRecipient.objects.get(user=accounts["switched"]).done_at is None
        assert NotificationPushActionToken.objects.get(recipient__user=accounts["switched"]).consumed_at is None

        # 3. Logged out: the CSRF bootstrap is refused, so no completion request is sent.
        loggedout = run_worker(live_server.url, payloads["loggedout"], {}, tmp_path)
        assert [entry["url"] for entry in loggedout["log"]] == ["/api/notifications/push/csrf/"]
        assert loggedout["closed"] is False
        assert NotificationRecipient.objects.get(user=accounts["loggedout"]).done_at is None
    finally:
        TYPE_REGISTRY.clear()
        TYPE_REGISTRY.update(types)
        TEXT_REGISTRY.clear()
        TEXT_REGISTRY.update(texts)
