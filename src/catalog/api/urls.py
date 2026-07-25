from django.urls import path

from catalog.api.views import PublishingGuideView, StreamDetailView, StreamListView

app_name = "catalog-api"

urlpatterns = [
    path("streams/", StreamListView.as_view(), name="stream-list"),
    path("streams/<uuid:stream_id>/", StreamDetailView.as_view(), name="stream-detail"),
    path("tutorial/", PublishingGuideView.as_view(), name="tutorial"),
]
