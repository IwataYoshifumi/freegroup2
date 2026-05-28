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
    # Phase 1c-β-1（仕様書 rev5 §4.3 / §12.8）：拡張集合演算対応の新プレビュー API
    path(
        "lists/preview-v2/",
        views.PreviewV2View.as_view(),
        name="mailing_list_preview_v2",
    ),
    # Phase 1c-β-2a（仕様書 rev6 §4.5 / §4.7 / §12.8）：新規作成ウィザード
    # 1-B：リスト名・備考入力
    path(
        "lists/new/",
        views.NewListMetaView.as_view(),
        name="new_list_meta",
    ),
    # 1-C：タグ選択（新規作成モード）
    path(
        "lists/new/tags/",
        views.NewListTagSelectionView.as_view(),
        name="new_list_tag_selection",
    ),
    # 1-D：新規作成確認（GET 専用）
    path(
        "lists/new/tags/confirm/",
        views.NewListConfirmView.as_view(),
        name="new_list_confirm",
    ),
    # 新規作成確定（POST 専用、PRG）
    path(
        "lists/new/confirm/",
        views.NewListCommitView.as_view(),
        name="new_list_commit",
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
    # Phase 1c-α（仕様書 §3 / §10 / §12）：個別追加・個別削除
    # 選択画面（GET 表示・POST 確認画面へ snapshot 保存）
    path(
        "lists/<uuid:pk>/members/add/",
        views.MemberAddView.as_view(),
        name="list_member_add",
    ),
    path(
        "lists/<uuid:pk>/members/remove/",
        views.MemberRemoveView.as_view(),
        name="list_member_remove",
    ),
    # 確認画面（GET 専用、session の snapshot を表示）
    path(
        "lists/<uuid:pk>/members/add/confirm/",
        views.MemberAddConfirmView.as_view(),
        name="list_member_add_confirm",
    ),
    path(
        "lists/<uuid:pk>/members/remove/confirm/",
        views.MemberRemoveConfirmView.as_view(),
        name="list_member_remove_confirm",
    ),
    # 確定エンドポイント（POST 専用、bulk_create / delete → session クリア → 詳細へ PRG）
    path(
        "lists/<uuid:pk>/members/confirm-add/",
        views.MemberAddCommitView.as_view(),
        name="list_member_commit_add",
    ),
    path(
        "lists/<uuid:pk>/members/confirm-remove/",
        views.MemberRemoveCommitView.as_view(),
        name="list_member_commit_remove",
    ),
    # MailingConfig 編集（§5.1 No.38 改 / §4.13 シングルトン）
    path("config/", views.MailingConfigEditView.as_view(), name="config_edit"),
]
