from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
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
]
