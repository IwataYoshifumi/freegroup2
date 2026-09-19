"""パーソンリスト（PersonList）URL ルーティング（仕様書 v1.6 §2.3.3 No.21〜24）。"""

from django.urls import path

from . import person_list_views as views

app_name = "person_lists"

urlpatterns = [
    path("", views.PersonListListView.as_view(), name="person_list_list"),
    path("create/", views.PersonListCreateView.as_view(), name="person_list_create"),
    path("<uuid:pk>/", views.PersonListDetailView.as_view(), name="person_list_detail"),
    path("<uuid:pk>/update/", views.PersonListUpdateView.as_view(), name="person_list_update"),
    path("<uuid:pk>/delete/", views.PersonListDeleteView.as_view(), name="person_list_delete"),
]
