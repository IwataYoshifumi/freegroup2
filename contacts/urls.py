"""contacts アプリの URL ルーティング（仕様書 v1.4.2 §11.3）。

定義する URL：
  - contact_detail（11 番、D-3b）: Contact 詳細画面（GET）
  - ajax_update_field（D-3c）: 1 フィールド値修正 + 自動 confirmed 化（POST）
  - ajax_confirm_fields（D-3c）: confidence 確認のみ（POST、個別 / 一括両用）
"""

from django.urls import path

from . import views


app_name = "contacts"


urlpatterns = [
    path(
        "<uuid:pk>/",
        views.ContactDetailView.as_view(),
        name="contact_detail",
    ),
    path(
        "<uuid:pk>/ajax-update-field/",
        views.ContactAjaxUpdateFieldView.as_view(),
        name="ajax_update_field",
    ),
    path(
        "<uuid:pk>/ajax-confirm-fields/",
        views.ContactAjaxConfirmFieldsView.as_view(),
        name="ajax_confirm_fields",
    ),
]
