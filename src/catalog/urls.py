from django.urls import path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.home, name="home"),
    path("streams/<uuid:stream_id>/", views.stream_detail, name="stream-detail"),
    path(
        "streams/<uuid:stream_id>/thumbnail/",
        views.stream_thumbnail,
        name="stream-thumbnail",
    ),
    path("tutorial/", views.tutorial, name="tutorial"),
]
