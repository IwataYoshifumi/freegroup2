"""tags アプリの URL ルーティング（仕様書 v1.6 §5.1 No.20〜26 / §6.2.5 / §6.2.6）。"""

from django.urls import path

from . import views

app_name = "tags"


urlpatterns = [
    # タグカテゴリ管理（§6.2.5、URL 名はコード君A 判断）
    path(
        "categories/",
        views.TagCategoryListView.as_view(),
        name="tag_category_list",
    ),
    path(
        "categories/create/",
        views.TagCategoryCreateView.as_view(),
        name="tag_category_create",
    ),
    path(
        "categories/<uuid:pk>/",
        views.TagCategoryDetailView.as_view(),
        name="tag_category_detail",
    ),
    path(
        "categories/<uuid:pk>/update/",
        views.TagCategoryUpdateView.as_view(),
        name="tag_category_update",
    ),
    path(
        "categories/<uuid:pk>/delete/",
        views.TagCategoryDeleteView.as_view(),
        name="tag_category_delete",
    ),
    path(
        "categories/<uuid:pk>/unarchive/",
        views.TagCategoryUnarchiveView.as_view(),
        name="tag_category_unarchive",
    ),
    path(
        "categories/reorder/",
        views.TagCategoryReorderView.as_view(),
        name="tag_category_reorder",
    ),
    # タグ管理（§5.1 No.20〜24、Phase 1b-δ で tag_detail 追加）
    path("", views.TagListView.as_view(), name="tag_list"),
    path("create/", views.TagCreateView.as_view(), name="tag_create"),
    path(
        "<uuid:pk>/",
        views.TagDetailView.as_view(),
        name="tag_detail",
    ),
    path(
        "<uuid:pk>/update/",
        views.TagUpdateView.as_view(),
        name="tag_update",
    ),
    path(
        "<uuid:pk>/delete/",
        views.TagDeleteView.as_view(),
        name="tag_delete",
    ),
    path(
        "<uuid:pk>/unarchive/",
        views.TagUnarchiveView.as_view(),
        name="tag_unarchive",
    ),
    # AJAX タグ付与・解除（§5.1 No.25・No.26、一括更新）
    path("assign/", views.TagAssignView.as_view(), name="tag_assign"),
    path("unassign/", views.TagUnassignView.as_view(), name="tag_unassign"),
    path("bulk-assign/", views.TagBulkAssignView.as_view(), name="tag_bulk_assign"),
    # 検索結果一括タグ付け（§6.2.6、URL 名はコード君A 判断）
    path("bulk-tagging/", views.BulkTaggingView.as_view(), name="bulk_tagging"),
    # タグ固定モード：タグ詳細から「このタグを人に付与」で遷移。tag をパスに乗せる
    # （クエリ/JS に依存させず固定を URL で保証）。pk があれば固定モード。
    path(
        "<uuid:pk>/bulk-tagging/",
        views.BulkTaggingView.as_view(),
        name="bulk_tagging_for_tag",
    ),
]
