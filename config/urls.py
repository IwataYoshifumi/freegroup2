"""URL configuration for config project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from home.views import HomeView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", HomeView.as_view(), name="home"),
    path("accounts/", include("accounts.urls")),
    path("cards/", include("cards.urls", namespace="cards")),
    path("originals/", include("cards.originals_urls", namespace="originals")),
    path("contacts/", include("contacts.urls", namespace="contacts")),
    path("persons/", include("persons.urls", namespace="persons")),
    path("duplicates/", include("duplicates.urls", namespace="duplicates")),
    path("tags/", include("tags.urls", namespace="tags")),
    path("mailings/", include("mailings.urls", namespace="mailings")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
