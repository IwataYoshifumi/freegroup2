from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("upload/", views.UploadView.as_view(), name="card_upload"),
    path("list/", views.placeholder_view, name="card_list"),
]
