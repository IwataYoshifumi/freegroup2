from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.CardListView.as_view(), name="card_list"),
    path("upload/", views.UploadView.as_view(), name="card_upload"),
    path("<uuid:pk>/", views.CardDetailView.as_view(), name="card_detail"),
    path("<uuid:pk>/delete/", views.CardDeleteView.as_view(), name="card_delete"),
]
