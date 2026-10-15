from django.urls import path
from .views import CatalogueView,AvailabilityView,ChatListCreateView,ChatDetailView,TurnsView
urlpatterns=[path("assistant/catalogue/",CatalogueView.as_view(),name="assistant-catalogue"),path("assistant/status/",AvailabilityView.as_view(),name="assistant-status"),path("assistant/chats/",ChatListCreateView.as_view(),name="assistant-chats"),path("assistant/chats/<uuid:chat_id>/",ChatDetailView.as_view(),name="assistant-chat"),path("assistant/chats/<uuid:chat_id>/turns/",TurnsView.as_view(),name="assistant-turns")]
