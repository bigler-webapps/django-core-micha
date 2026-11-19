from django.urls import path

from .views import (
    AccountCollectionView, AccountView, DiscoveryView, FixtureView, RecoverView,
    RunCollectionView, RunView, RecoveryMessageView,
)
from .views import RunActionView


class ResetView(RunActionView):
    operation = "reset"


class CloseView(RunActionView):
    operation = "close"


urlpatterns = [
    path("discovery/", DiscoveryView.as_view()),
    path("runs/", RunCollectionView.as_view()),
    path("runs/<uuid:run>/", RunView.as_view()),
    path("runs/<uuid:run>/recover/", RecoverView.as_view()),
    path("runs/<uuid:run>/reset/", ResetView.as_view()),
    path("runs/<uuid:run>/close/", CloseView.as_view()),
    path("runs/<uuid:run>/accounts/", AccountCollectionView.as_view()),
    path("runs/<uuid:run>/accounts/<uuid:account>/", AccountView.as_view()),
    path("runs/<uuid:run>/accounts/<uuid:account>/recovery-message/", RecoveryMessageView.as_view()),
    path("runs/<uuid:run>/fixtures/", FixtureView.as_view()),
]
