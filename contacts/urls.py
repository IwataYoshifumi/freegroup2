"""contacts アプリの URL ルーティング（仕様書 v1.4.2 §11.3）。

定義する URL：
  - contact_list（v1.4.2 仕様変更追加）: Contact 一覧画面（GET）
  - contact_create（10 番、D-Form ステップ3a）: 手動 Contact 新規作成画面（GET/POST）
  - contact_detail（11 番、D-3b）: Contact 詳細画面（GET）
  - contact_preview（14 番、D-Form ステップ3a）: Contact プレビュー HTML フラグメント（GET）
  - contact_update_active（13 番、D-Form ステップ1）: active Contact 修正画面（GET/POST）
  - ajax_update_field（D-3c）: 1 フィールド値修正 + 自動 confirmed 化（POST）
  - ajax_confirm_fields（D-3c）: confidence 確認のみ（POST、個別 / 一括両用）
"""

from django.urls import path

from . import views


app_name = "contacts"


urlpatterns = [
    path("", views.ContactListView.as_view(), name="contact_list"),
    # create/ は <uuid:pk>/ より前に配置（uuid コンバータと衝突しないが念のため）
    path("create/", views.ContactCreateView.as_view(), name="contact_create"),
    path(
        "<uuid:pk>/",
        views.ContactDetailView.as_view(),
        name="contact_detail",
    ),
    path(
        "<uuid:pk>/preview/",
        views.PreviewContactView.as_view(),
        name="contact_preview",
    ),
    path(
        "<uuid:pk>/update-active/",
        views.UpdateActiveContactView.as_view(),
        name="contact_update_active",
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
