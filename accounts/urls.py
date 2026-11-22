from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_login_security, views_password, views_two_factor

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/verify/", views.LoginVerifyView.as_view(), name="login_verify"),
    path("two-factor/", views_two_factor.TwoFactorSettingsView.as_view(), name="two_factor"),
    path("two-factor/setup/", views_two_factor.TwoFactorSetupView.as_view(), name="two_factor_setup"),
    path("two-factor/recovery-codes/", views_two_factor.TwoFactorRecoveryCodesView.as_view(), name="two_factor_recovery_codes"),
    path("two-factor/disable/", views_two_factor.TwoFactorDisableView.as_view(), name="two_factor_disable"),
    path("devices/<int:device_id>/revoke/", views_two_factor.LoginDeviceRevokeView.as_view(), name="login_device_revoke"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", views_password.PasswordChangeView.as_view(), name="password_change"),
    path("password/change/done/", views_password.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password/reset/", views_password.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/done/", views_password.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password/reset/<uidb64>/<token>/",
        views_password.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        views_password.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("profile/signature/", views.SignatureUpdateView.as_view(), name="signature_update"),
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
    path("users/<int:user_id>/two-factor/reset/", views_login_security.TwoFactorResetView.as_view(), name="two_factor_reset"),
    path("users/<int:user_id>/unlock/", views_login_security.AccountUnlockView.as_view(), name="account_unlock"),
    path("security/", views_login_security.SecuritySettingView.as_view(), name="security_setting"),
    path("login-attempts/", views_login_security.LoginAttemptListView.as_view(), name="login_attempt_list"),
    path("roles/", views.RoleListView.as_view(), name="role_list"),
    path("roles/new/", views.RoleCreateView.as_view(), name="role_create"),
    path("roles/<int:pk>/edit/", views.RoleUpdateView.as_view(), name="role_update"),
    path("roles/<int:pk>/delete/", views.RoleDeleteView.as_view(), name="role_delete"),
    path("groups/", views.GroupListView.as_view(), name="group_list"),
    path("groups/<int:pk>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("permissions/", views.PermissionListView.as_view(), name="permission_list"),
]
