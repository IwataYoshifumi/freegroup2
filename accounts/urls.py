from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("user-person/confirm/<uuid:person_id>/", views.LinkUserPersonConfirmView.as_view(), name="link_user_person_confirm"),
    path("user-person/start-link/", views.StartLinkFlowView.as_view(), name="start_link_flow"),
    path(
        "users/<int:user_id>/link/<uuid:person_id>/",
        views.LinkUserPersonView.as_view(),
        name="link_user_person",
    ),
    path(
        "users/<int:user_id>/unlink/",
        views.UnlinkUserPersonView.as_view(),
        name="unlink_user_person",
    ),
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/<int:user_id>/", views.UserDetailView.as_view(), name="user_detail"),
    path("users/<int:user_id>/retire/", views.RetireUserView.as_view(), name="retire_user"),
    path("users/<int:user_id>/assign-role/", views.AssignRoleView.as_view(), name="assign_role"),
    path("roles/", views.RoleListView.as_view(), name="role_list"),
    path("roles/new/", views.RoleCreateView.as_view(), name="role_create"),
    path("roles/<int:pk>/edit/", views.RoleUpdateView.as_view(), name="role_update"),
    path("roles/<int:pk>/delete/", views.RoleDeleteView.as_view(), name="role_delete"),
    path("groups/", views.GroupListView.as_view(), name="group_list"),
    path("groups/<int:pk>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("permissions/", views.PermissionListView.as_view(), name="permission_list"),
]
