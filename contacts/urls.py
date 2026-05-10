"""contacts アプリの URL ルーティング（仕様書 v1.4.2 §11.3）。

D-3c で追加した AJAX 2 エンドポイントを定義する：
  - ajax_update_field: 1 フィールド値修正 + 自動 confirmed 化
  - ajax_confirm_fields: confidence 確認のみ（個別 / 一括両用）
"""

from django.urls import path

from . import views


app_name = "contacts"


urlpatterns = [
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
