from django.urls import path

from deals.views import (
    DealAddPersonView,
    DealAddUserView,
    DealArchiveView,
    DealCloseView,
    DealCreateView,
    DealDeletePersonView,
    DealDeleteUserView,
    DealDetailView,
    DealListView,
    DealReassignOwnerView,
    DealReassignPrimaryPersonView,
    DealUpdateView,
)

app_name = "deals"

urlpatterns = [
    path("", DealListView.as_view(), name="deal_list"),
    path("create/", DealCreateView.as_view(), name="deal_create"),
    path("<uuid:pk>/", DealDetailView.as_view(), name="deal_detail"),
    path("<uuid:pk>/edit/", DealUpdateView.as_view(), name="deal_update"),
    path("<uuid:pk>/close/", DealCloseView.as_view(), name="deal_close"),
    path("<uuid:pk>/reassign-owner/", DealReassignOwnerView.as_view(), name="deal_reassign_owner"),
    path(
        "<uuid:pk>/reassign-primary-person/",
        DealReassignPrimaryPersonView.as_view(),
        name="deal_reassign_primary_person",
    ),
    path("<uuid:pk>/archive/", DealArchiveView.as_view(), name="deal_archive"),
    path("<uuid:pk>/persons/add/", DealAddPersonView.as_view(), name="deal_add_person"),
    path(
        "<uuid:pk>/persons/<uuid:person_rel_id>/delete/",
        DealDeletePersonView.as_view(),
        name="deal_delete_person",
    ),
    path("<uuid:pk>/users/add/", DealAddUserView.as_view(), name="deal_add_user"),
    path(
        "<uuid:pk>/users/<uuid:user_rel_id>/delete/",
        DealDeleteUserView.as_view(),
        name="deal_delete_user",
    ),
]
