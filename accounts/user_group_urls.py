"""ユーザーグループ（UserGroup）URL ルーティング（仕様書 v1.6 §2.3.2 No.10〜16）。"""

from django.urls import path

from . import user_group_views as views

app_name = "user_groups"

urlpatterns = [
    path("", views.UserGroupListView.as_view(), name="user_group_list"),
    path("create/", views.UserGroupCreateView.as_view(), name="user_group_create"),
    path("<uuid:pk>/", views.UserGroupDetailView.as_view(), name="user_group_detail"),
    path("<uuid:pk>/update/", views.UserGroupUpdateView.as_view(), name="user_group_update"),
    path("<uuid:pk>/delete/", views.UserGroupDeleteView.as_view(), name="user_group_delete"),
    path("<uuid:pk>/add-member/", views.UserGroupAddMemberView.as_view(), name="user_group_add_member"),
    path("<uuid:pk>/remove-member/", views.UserGroupRemoveMemberView.as_view(), name="user_group_remove_member"),
]
