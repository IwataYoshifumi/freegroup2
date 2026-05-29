"""mailings アプリの URL ルーティング（仕様書 v1.6 §5.1 / §5.2.1 / §6.2.4）。

Phase 1b-γ：MailingList CRUD + 凍結/プレビュー/対象外 AJAX + MailingConfig 編集。
発注書 §1.2：No.38 を /settings/system/ → /mailings/config/ に改名。
発注書 §1.3：No.32（メンバー個別追加 AJAX）は実装しない（凍結方式の徹底）。

Phase 4（仕様書 v1.6 §5.1 No.34 / §5.2.1）：クリック中継 `/t/<token>/` を
プレフィックスなしの極短 URL として追加。Django 制約（同一 instance_namespace の
複数 URLconf マージ不可）により、本ファイル 1 つに通常 URL（`/mailings/` 配下）と
極短 URL（プレフィックスなし）を同居させる。config/urls.py 側は空 prefix で
include する形に変更。各 path は最終 URL を明示する文字列を持つ。
"""

from django.urls import path

from . import views

app_name = "mailings"


urlpatterns = [
    # ==================================================================
    # 仕様書 v1.6 §5.1 No.34（Phase 4）：クリック中継、極短 URL（§5.2.1）。
    # GET / HEAD のみ。HEAD はメーラープリフェッチ用に 302 を返すが非有効クリック扱い。
    # ==================================================================
    path(
        "t/<str:token>/",
        views.TrackingRedirectView.as_view(),
        name="tracking_redirect",
    ),
    # ==================================================================
    # 仕様書 v1.6 §5.1 No.35〜37 / No.41（Phase 5）：配信停止リンク、極短 URL。
    # GET = 確認画面表示・accessed_at 更新のみ（プリフェッチ事故防止、§9.2 / §5.2.2）
    # POST confirm = 停止確定、unsubscribed_at 更新、done へ 302
    # GET done = 完了画面（UnsubscribeLink は読まない、§4.5A.2）
    # GET /u/ = トークン無し独立ルート、案内表示のみ（メアド手入力 UI なし、§4.5A.2.1）
    # ==================================================================
    path(
        "u/",
        views.UnsubscribeTokenlessView.as_view(),
        name="unsubscribe_page_tokenless",
    ),
    path(
        "u/<str:token>/",
        views.UnsubscribePageView.as_view(),
        name="unsubscribe_page",
    ),
    path(
        "u/<str:token>/confirm/",
        views.UnsubscribeConfirmView.as_view(),
        name="unsubscribe_confirm",
    ),
    path(
        "u/<str:token>/done/",
        views.UnsubscribeDoneView.as_view(),
        name="unsubscribe_done",
    ),
    # ==================================================================
    # /mailings/ 配下：通常の管理画面・AJAX エンドポイント
    # ==================================================================
    # Phase 5（仕様書 §5.1 No.17/18/19）：SuppressedEmail 管理
    path(
        "mailings/suppressed/",
        views.SuppressedEmailListView.as_view(),
        name="suppressed_list",
    ),
    path(
        "mailings/suppressed/add/",
        views.SuppressedEmailCreateView.as_view(),
        name="suppressed_create",
    ),
    path(
        "mailings/suppressed/<uuid:pk>/",
        views.SuppressedEmailDetailView.as_view(),
        name="suppressed_detail",
    ),
    # Phase 5（§9.8 / §9.3.1）：管理者代行・解除（Person 詳細画面から呼ぶ）
    path(
        "mailings/persons/<uuid:pk>/unsubscribe-by-admin/",
        views.PersonUnsubscribeByAdminView.as_view(),
        name="person_unsubscribe_by_admin",
    ),
    path(
        "mailings/persons/<uuid:pk>/cancel-unsubscribe/",
        views.PersonCancelUnsubscribeView.as_view(),
        name="person_cancel_unsubscribe",
    ),
    # Phase 5（§10.1 / §10.2）：バウンス Webhook 受け口（外部 MTA 用、最小実装）
    path(
        "mailings/bounce/webhook/",
        views.BounceWebhookView.as_view(),
        name="bounce_webhook",
    ),
    # Phase 6（仕様書 §5.1 No.6 / No.12〜16、§13 / §6.2.2 / §6.2.3 / §13.2）：
    # 配信レポート画面 + CSV ダウンロード。レポートは読み取り専用集計、
    # 配信停止数の出所は UnsubscribeLink（DeliveryHistory.status='unsubscribed' は使わない、§指示書確定事項）。
    path(
        "mailings/campaigns/",
        views.CampaignListView.as_view(),
        name="campaign_list",
    ),
    path(
        "mailings/campaigns/<uuid:pk>/report/",
        views.CampaignReportView.as_view(),
        name="campaign_report",
    ),
    path(
        "mailings/campaigns/<uuid:pk>/report/clicked/",
        views.CampaignReportClickedListView.as_view(),
        name="campaign_report_clicked",
    ),
    path(
        "mailings/campaigns/<uuid:pk>/report/bounced/",
        views.CampaignReportBouncedListView.as_view(),
        name="campaign_report_bounced",
    ),
    path(
        "mailings/campaigns/<uuid:pk>/report/unsubscribed/",
        views.CampaignReportUnsubscribedListView.as_view(),
        name="campaign_report_unsubscribed",
    ),
    path(
        "mailings/campaigns/<uuid:pk>/report/csv/",
        views.CampaignReportCSVView.as_view(),
        name="campaign_report_csv",
    ),
    # MailingList CRUD（§5.1 No.27〜31 + No.33 改）
    path(
        "mailings/lists/", views.MailingListListView.as_view(), name="mailing_list_list"
    ),
    path(
        "mailings/lists/create/",
        views.MailingListCreateView.as_view(),
        name="mailing_list_create",
    ),
    path(
        "mailings/lists/<uuid:pk>/",
        views.MailingListDetailView.as_view(),
        name="mailing_list_detail",
    ),
    path(
        "mailings/lists/<uuid:pk>/update/",
        views.MailingListUpdateView.as_view(),
        name="mailing_list_update",
    ),
    path(
        "mailings/lists/<uuid:pk>/delete/",
        views.MailingListDeleteView.as_view(),
        name="mailing_list_delete",
    ),
    path(
        "mailings/lists/<uuid:pk>/unarchive/",
        views.MailingListUnarchiveView.as_view(),
        name="mailing_list_unarchive",
    ),
    # AJAX エンドポイント
    path(
        "mailings/lists/freeze/",
        views.MailingListFreezeView.as_view(),
        name="mailing_list_freeze",
    ),
    path(
        "mailings/lists/preview/",
        views.MailingListPreviewView.as_view(),
        name="mailing_list_preview",
    ),
    # Phase 1c-β-1（仕様書 rev5 §4.3 / §12.8）：拡張集合演算対応の新プレビュー API
    path(
        "mailings/lists/preview-v2/",
        views.PreviewV2View.as_view(),
        name="mailing_list_preview_v2",
    ),
    # Phase 1c-β-2a（仕様書 rev6 §4.5 / §4.7 / §12.8）：新規作成ウィザード
    # 1-B：リスト名・備考入力
    path(
        "mailings/lists/new/",
        views.NewListMetaView.as_view(),
        name="new_list_meta",
    ),
    # 1-C：タグ選択（新規作成モード）
    path(
        "mailings/lists/new/tags/",
        views.NewListTagSelectionView.as_view(),
        name="new_list_tag_selection",
    ),
    # 1-D：新規作成確認（GET 専用）
    path(
        "mailings/lists/new/tags/confirm/",
        views.NewListConfirmView.as_view(),
        name="new_list_confirm",
    ),
    # 新規作成確定（POST 専用、PRG）
    path(
        "mailings/lists/new/confirm/",
        views.NewListCommitView.as_view(),
        name="new_list_commit",
    ),
    path(
        "mailings/lists/member/remove/",
        views.MailingListMemberRemoveView.as_view(),
        name="mailing_list_member_remove",
    ),
    # rev14.1 §5.1 No.32/33（Phase 1b-ε.6 追加）：未凍結時の手動メンバー追加・削除
    path(
        "mailings/lists/<uuid:pk>/add-member/",
        views.MailingListAddMemberView.as_view(),
        name="list_add_member",
    ),
    path(
        "mailings/lists/<uuid:pk>/remove-member/",
        views.MailingListRemoveMemberView.as_view(),
        name="list_remove_member",
    ),
    # rev14.1 §11.3.6（Phase 1b-ε.6 追補 修正 4）：本体（name/description）AJAX 自動保存
    path(
        "mailings/lists/<uuid:pk>/update-meta/",
        views.MailingListUpdateMetaView.as_view(),
        name="list_update_meta",
    ),
    # Phase 1c-α（仕様書 §3 / §10 / §12）：個別追加・個別削除
    # 選択画面（GET 表示・POST 確認画面へ snapshot 保存）
    path(
        "mailings/lists/<uuid:pk>/members/add/",
        views.MemberAddView.as_view(),
        name="list_member_add",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/remove/",
        views.MemberRemoveView.as_view(),
        name="list_member_remove",
    ),
    # 確認画面（GET 専用、session の snapshot を表示）
    path(
        "mailings/lists/<uuid:pk>/members/add/confirm/",
        views.MemberAddConfirmView.as_view(),
        name="list_member_add_confirm",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/remove/confirm/",
        views.MemberRemoveConfirmView.as_view(),
        name="list_member_remove_confirm",
    ),
    # 確定エンドポイント（POST 専用、bulk_create / delete → session クリア → 詳細へ PRG）
    path(
        "mailings/lists/<uuid:pk>/members/confirm-add/",
        views.MemberAddCommitView.as_view(),
        name="list_member_commit_add",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/confirm-remove/",
        views.MemberRemoveCommitView.as_view(),
        name="list_member_commit_remove",
    ),
    # Phase 1c-β-3a（仕様書 rev6 §4.6 / §12.8）：タグで追加
    path(
        "mailings/lists/<uuid:pk>/members/add-by-tag/",
        views.MemberAddByTagView.as_view(),
        name="list_member_add_by_tag",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/add-by-tag/confirm/",
        views.MemberAddByTagConfirmView.as_view(),
        name="list_member_add_by_tag_confirm",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/confirm-add-by-tag/",
        views.MemberAddByTagCommitView.as_view(),
        name="list_member_commit_add_by_tag",
    ),
    # Phase 1c-β-3b（仕様書 rev6 §4.6.5 / §12.8）：タグで除外
    path(
        "mailings/lists/<uuid:pk>/members/remove-by-tag/",
        views.MemberRemoveByTagView.as_view(),
        name="list_member_remove_by_tag",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/remove-by-tag/confirm/",
        views.MemberRemoveByTagConfirmView.as_view(),
        name="list_member_remove_by_tag_confirm",
    ),
    path(
        "mailings/lists/<uuid:pk>/members/confirm-remove-by-tag/",
        views.MemberRemoveByTagCommitView.as_view(),
        name="list_member_commit_remove_by_tag",
    ),
    # MailingConfig 編集（§5.1 No.38 改 / §4.13 シングルトン）
    path("mailings/config/", views.MailingConfigEditView.as_view(), name="config_edit"),
]
