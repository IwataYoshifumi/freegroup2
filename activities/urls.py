from django.urls import path

from activities.views import (
    ActivityAddPersonView,
    ActivityAddUserView,
    ActivityArchiveView,
    ActivityCreateView,
    ActivityDeletePersonView,
    ActivityDeleteUserView,
    ActivityDetailView,
    ActivityListView,
    ActivityUpdateView,
    CampaignUnfollowedListView,
)

app_name = "activities"

urlpatterns = [
    path("", ActivityListView.as_view(), name="activity_list"),
    path("create/", ActivityCreateView.as_view(), name="activity_create"),
    path("<uuid:pk>/", ActivityDetailView.as_view(), name="activity_detail"),
    path("<uuid:pk>/edit/", ActivityUpdateView.as_view(), name="activity_update"),
    path("<uuid:pk>/archive/", ActivityArchiveView.as_view(), name="activity_archive"),
    path(
        "campaigns/<uuid:campaign_id>/unfollowed/",
        CampaignUnfollowedListView.as_view(),
        name="campaign_unfollowed_list",
    ),
    path("<uuid:pk>/persons/add/", ActivityAddPersonView.as_view(), name="activity_add_person"),
    path(
        "<uuid:pk>/persons/<uuid:person_rel_id>/delete/",
        ActivityDeletePersonView.as_view(),
        name="activity_delete_person",
    ),
    path("<uuid:pk>/users/add/", ActivityAddUserView.as_view(), name="activity_add_user"),
    path(
        "<uuid:pk>/users/<uuid:user_rel_id>/delete/",
        ActivityDeleteUserView.as_view(),
        name="activity_delete_user",
    ),
]
