"""duplicates アプリの URL ルーティング（仕様書 §11.3、15 / 16 / 17 番）。

- duplicate_group_list（15 番、D-4e）: 重複候補グループ一覧（GET）
- duplicate_group_detail（16 番、D-4e）: 重複候補グループ詳細（GET）
- duplicate_group_review（17 番、D-4b）: 重複候補レビュー画面（GET、POST は D-4c）

19/20/21 番（マージログ系）は D-4f で別途追加予定。
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
]
