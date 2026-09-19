"""permissions アプリの URL ルーティング（仕様書 v1.6 §2.3.1）。"""

from django.urls import path

from . import views

app_name = "permissions"

urlpatterns = [
    path("", views.AccessListListView.as_view(), name="access_list_list"),
    path("create/", views.AccessListCreateView.as_view(), name="access_list_create"),
    path("<uuid:pk>/", views.AccessListDetailView.as_view(), name="access_list_detail"),
    path("<uuid:pk>/update/", views.AccessListUpdateView.as_view(), name="access_list_update"),
    path("<uuid:pk>/delete/", views.AccessListDeleteView.as_view(), name="access_list_delete"),
    path("<uuid:pk>/entries/create/", views.ACLEntryCreateView.as_view(), name="acl_entry_create"),
    path("<uuid:pk>/entries/<uuid:entry_pk>/update/", views.ACLEntryUpdateView.as_view(), name="acl_entry_update"),
    path("<uuid:pk>/entries/<uuid:entry_pk>/delete/", views.ACLEntryDeleteView.as_view(), name="acl_entry_delete"),
    path("<uuid:pk>/entries/reorder/", views.ACLEntryReorderView.as_view(), name="acl_entry_reorder"),
]
