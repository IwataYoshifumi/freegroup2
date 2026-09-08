from django.urls import path

from attachments.views import (
    AttachmentDeleteView,
    AttachmentMemoUpdateView,
    AttachmentUploadView,
    ProtectedFileDownloadView,
)

app_name = "attachments"

urlpatterns = [
    path(
        "<uuid:pk>/download/",
        ProtectedFileDownloadView.as_view(),
        {"model_name": "attachments"},
        name="attachment_download",
    ),
    path(
        "<str:model_name>/<uuid:pk>/download/",
        ProtectedFileDownloadView.as_view(),
        name="attachment_download_model",
    ),
    path("upload/", AttachmentUploadView.as_view(), name="attachment_upload"),
    path("<uuid:pk>/delete/", AttachmentDeleteView.as_view(), name="attachment_delete"),
    path("<uuid:pk>/memo/", AttachmentMemoUpdateView.as_view(), name="attachment_update_memo"),
]
