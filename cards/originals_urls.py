from django.urls import path

from . import views

app_name = "originals"

urlpatterns = [
    path("", views.OriginalListView.as_view(), name="original_list"),
    path("<uuid:pk>/", views.OriginalDetailView.as_view(), name="original_detail"),
    path("<uuid:pk>/recalc-debug/", views.RecalcDebugView.as_view(), name="recalc_debug"),
    path("<uuid:pk>/manual-crop/", views.ManualCropView.as_view(), name="manual_crop"),
    path(
        "<uuid:pk>/manual-crop/preview/",
        views.ManualPreviewView.as_view(),
        name="manual_crop_preview",
    ),
]
