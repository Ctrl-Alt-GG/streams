from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView
from health_check.views import HealthCheckView
from rest_framework.permissions import IsAdminUser

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("catalog.api.urls")),
    path(
        "api/schema/",
        SpectacularAPIView.as_view(permission_classes=(IsAdminUser,)),
        name="schema",
    ),
    path(
        "api/docs/",
        SpectacularSwaggerSplitView.as_view(
            url_name="schema",
            permission_classes=(IsAdminUser,),
        ),
        name="api-docs",
    ),
    path(
        "health/",
        HealthCheckView.as_view(
            checks=("health_check.checks.Cache", "health_check.checks.Database")
        ),
        name="health",
    ),
]

urlpatterns += i18n_patterns(
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("catalog.urls")),
    prefix_default_language=False,
)

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
