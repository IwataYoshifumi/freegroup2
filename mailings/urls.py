"""mailings アプリの URL ルーティング（仕様書 v1.6 §5.1 No.27〜33 / No.38 / §6.2.4）。

Phase 1b-γ：MailingList CRUD + 凍結/プレビュー/対象外 AJAX + MailingConfig 編集。
発注書 §1.2：No.38 を /settings/system/ → /mailings/config/ に改名。
発注書 §1.3：No.32（メンバー個別追加 AJAX）は実装しない（凍結方式の徹底）。
"""

from django.urls import path

from . import views

app_name = "mailings"


urlpatterns = [
    # MailingList CRUD（§5.1 No.27〜31 + No.33 改）
    path("lists/", views.MailingListListView.as_view(), name="mailing_list_list"),
    path(
        "lists/create/",
        views.MailingListCreateView.as_view(),
        name="mailing_list_create",
    ),
    path(
        "lists/<uuid:pk>/",
        views.MailingListDetailView.as_view(),
        name="mailing_list_detail",
    ),
    path(
        "lists/<uuid:pk>/update/",
        views.MailingListUpdateView.as_view(),
        name="mailing_list_update",
    ),
    path(
        "lists/<uuid:pk>/delete/",
        views.MailingListDeleteView.as_view(),
        name="mailing_list_delete",
    ),
    path(
        "lists/<uuid:pk>/unarchive/",
        views.MailingListUnarchiveView.as_view(),
        name="mailing_list_unarchive",
    ),
    # AJAX エンドポイント
    path(
        "lists/freeze/",
        views.MailingListFreezeView.as_view(),
        name="mailing_list_freeze",
    ),
    path(
        "lists/preview/",
        views.MailingListPreviewView.as_view(),
        name="mailing_list_preview",
    ),
    path(
        "lists/member/remove/",
        views.MailingListMemberRemoveView.as_view(),
        name="mailing_list_member_remove",
    ),
    # rev14.1 §5.1 No.32/33（Phase 1b-ε.6 追加）：未凍結時の手動メンバー追加・削除
    path(
        "lists/<uuid:pk>/add-member/",
        views.MailingListAddMemberView.as_view(),
        name="list_add_member",
    ),
    path(
        "lists/<uuid:pk>/remove-member/",
        views.MailingListRemoveMemberView.as_view(),
        name="list_remove_member",
    ),
    # rev14.1 §11.3.6（Phase 1b-ε.6 追補 修正 4）：本体（name/description）AJAX 自動保存
    path(
        "lists/<uuid:pk>/update-meta/",
        views.MailingListUpdateMetaView.as_view(),
        name="list_update_meta",
    ),
    # MailingConfig 編集（§5.1 No.38 改 / §4.13 シングルトン）
    path("config/", views.MailingConfigEditView.as_view(), name="config_edit"),
]
