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
    # 仕様書 v1.6 §5.1 / §5.2.1：mailings アプリは通常 URL（`/mailings/` 配下）と
    # 極短 URL（プレフィックスなしの `/t/<token>/` 等）を 1 つの URLconf に統合し、
    # 空 prefix で include する。各 path 側で必要に応じて "mailings/" プレフィックスを
    # 明示する設計（Django の「同一 namespace で複数 URLconf 登録不可」制約の回避）。
    path("", include("mailings.urls", namespace="mailings")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
