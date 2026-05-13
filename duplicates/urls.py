"""duplicates アプリの URL ルーティング（仕様書 §11.3、15 / 16 / 17 / 19 / 20 / 21 番）。

- duplicate_group_list（15 番、D-4e）: 重複候補グループ一覧（GET）
- duplicate_group_detail（16 番、D-4e）: 重複候補グループ詳細（GET）
- duplicate_group_review（17 番、D-4b/c/d）: 重複候補レビュー画面（GET / POST）
- merge_log_list（19 番、D-4f-1）: マージログ一覧（GET）
- merge_log_detail（20 番、D-4f-1）: マージログ詳細（GET）
- merge_log_confirm_undo（21 番、D-4f-2）: マージ復元確認 + 実行（GET / POST）
"""

from django.urls import path

from . import views


app_name = "duplicates"


urlpatterns = [
    path(
        "",
        views.DuplicateCandidateGroupListView.as_view(),
        name="duplicate_group_list",
    ),
    path(
        "groups/<uuid:group_id>/",
        views.DuplicateCandidateGroupDetailView.as_view(),
        name="duplicate_group_detail",
    ),
    path(
        "groups/<uuid:group_id>/review/",
        views.DuplicateCandidateGroupUpdateView.as_view(),
        name="duplicate_group_review",
    ),
    path(
        "merge-logs/",
        views.PersonMergeLogListView.as_view(),
        name="merge_log_list",
    ),
    path(
        "merge-logs/<uuid:pk>/",
        views.PersonMergeLogDetailView.as_view(),
        name="merge_log_detail",
    ),
    path(
        "merge-logs/<uuid:pk>/confirm-undo/",
        views.PersonMergeLogConfirmUndoView.as_view(),
        name="merge_log_confirm_undo",
    ),
]
