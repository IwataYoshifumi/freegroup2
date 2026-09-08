from django.urls import path

from companies.views import (
    CompanyArchiveView,
    CompanyCreateView,
    CompanyDetailView,
    CompanyDuplicateCandidateListView,
    CompanyListView,
    CompanyMarkDifferentView,
    CompanyMergeView,
    CompanyUpdateView,
)

app_name = "companies"

urlpatterns = [
    path("", CompanyListView.as_view(), name="company_list"),
    path("create/", CompanyCreateView.as_view(), name="company_create"),
    path("<uuid:pk>/", CompanyDetailView.as_view(), name="company_detail"),
    path("<uuid:pk>/edit/", CompanyUpdateView.as_view(), name="company_update"),
    path("<uuid:pk>/archive/", CompanyArchiveView.as_view(), name="company_archive"),
    path(
        "candidates/",
        CompanyDuplicateCandidateListView.as_view(),
        name="company_candidate_list",
    ),
    path(
        "candidates/",
        CompanyDuplicateCandidateListView.as_view(),
        name="candidate_list",
    ),
    path("merge/", CompanyMergeView.as_view(), name="company_merge"),
    path(
        "candidates/<uuid:pk>/different/",
        CompanyMarkDifferentView.as_view(),
        name="company_mark_different",
    ),
]
