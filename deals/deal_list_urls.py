"""案件リスト（DealList）URL ルーティング（仕様書 v1.6 §2.3.3 No.17〜20）。"""

from django.urls import path

from . import deal_list_views as views

app_name = "deal_lists"

urlpatterns = [
    path("", views.DealListListView.as_view(), name="deal_list_list"),
    path("create/", views.DealListCreateView.as_view(), name="deal_list_create"),
    path("<uuid:pk>/", views.DealListDetailView.as_view(), name="deal_list_detail"),
    path("<uuid:pk>/update/", views.DealListUpdateView.as_view(), name="deal_list_update"),
    path("<uuid:pk>/delete/", views.DealListDeleteView.as_view(), name="deal_list_delete"),
]
