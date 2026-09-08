from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from actionlogs.models import ActionLog
from activities.models import Activity
from activities.permissions import can_edit_activity
from attachments.forms import AttachmentMemoUpdateForm, AttachmentUploadForm
from attachments.models import Attachment
from attachments.permissions import (
    can_delete_attachment,
    can_edit_attachment,
    can_view_attachment,
)
from attachments.services import delete_attachment
from back_navigator.back_navigator import BackNavigator
from deals.models import Deal
from deals.permissions import can_edit_deal

MODEL_REGISTRY = {
    "attachments": (Attachment, "attachments.view_attachment"),
}


def get_registry_entry(model_name):
    entry = MODEL_REGISTRY.get(model_name)
    if entry is None:
        raise Http404
    return entry


class ProtectedFileDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """保護ストレージからの権限チェック付きファイル配信View（仕様書 v1.5 §4.3）。"""

    def get_permission_required(self):
        model_name = self.kwargs.get("model_name", "attachments")
        _model, permission = get_registry_entry(model_name)
        return (permission,)

    def get(self, request, pk, model_name="attachments"):
        model, _permission = get_registry_entry(model_name)
        obj = get_object_or_404(model, pk=pk)

        if not can_view_attachment(request.user, obj):
            raise PermissionDenied

        from pathlib import Path
        quoted_filename = quote(obj.original_filename)
        ext = Path(obj.original_filename).suffix
        ascii_fallback = f"attachment{ext}"
        response = FileResponse(
            obj.file.open("rb"),
            as_attachment=True,
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted_filename}'
        )
        return response


class AttachmentUploadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """添付ファイルアップロードView（仕様書 v1.5 §4.4.1）。"""

    permission_required = "attachments.add_attachment"

    def post(self, request, *args, **kwargs):
        deal_id = request.POST.get("deal_id") or request.GET.get("deal_id")
        activity_id = request.POST.get("activity_id") or request.GET.get("activity_id")

        deal = None
        activity = None
        redirect_url = reverse("home")

        if deal_id:
            deal = get_object_or_404(Deal, pk=deal_id)
            if not can_edit_deal(request.user, deal):
                raise PermissionDenied
            redirect_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        elif activity_id:
            activity = get_object_or_404(Activity, pk=activity_id)
            if not can_edit_activity(request.user, activity):
                raise PermissionDenied
            redirect_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        else:
            messages.error(request, "添付先の案件または活動が指定されていません。")
            return redirect(redirect_url)

        form = AttachmentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            attachment = form.save(commit=False)
            if deal:
                attachment.deal = deal
            if activity:
                attachment.activity = activity
            attachment.uploaded_by = request.user
            attachment.original_filename = form.cleaned_data["file"].name
            attachment.save()

            target_type = "deal" if deal else "activity"
            target_id = str(deal.id) if deal else str(activity.id)
            ActionLog.record(
                user=request.user,
                action="attachment_uploaded",
                content_object=attachment,
                data={
                    "original_filename": attachment.original_filename,
                    "target_type": target_type,
                    "target_id": target_id,
                },
            )
            messages.success(request, f"ファイル「{attachment.original_filename}」を添付しました。")
        else:
            for error_list in form.errors.values():
                for error in error_list:
                    messages.error(request, error)

        return redirect(redirect_url)


class AttachmentDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """添付ファイル削除View（仕様書 v1.5 §4.4.2）。"""

    permission_required = "attachments.delete_attachment"

    def dispatch(self, request, *args, **kwargs):
        self.attachment = get_object_or_404(Attachment, pk=kwargs["pk"])
        if not can_delete_attachment(request.user, self.attachment):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_redirect_url(self):
        if self.attachment.deal_id:
            return reverse("deals:deal_detail", kwargs={"pk": self.attachment.deal_id})
        elif self.attachment.activity_id:
            return reverse("activities:activity_detail", kwargs={"pk": self.attachment.activity_id})
        return reverse("home")

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "attachments/attachment_confirm_delete.html",
            {
                "attachment": self.attachment,
                "back": BackNavigator(request),
            },
        )

    def post(self, request, *args, **kwargs):
        redirect_url = self.get_redirect_url()
        filename = self.attachment.original_filename
        delete_attachment(self.attachment, user=request.user)
        messages.success(request, f"ファイル「{filename}」を削除しました。")
        return redirect(redirect_url)


class AttachmentMemoUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """添付ファイルメモ更新View（仕様書 v1.5 §4.4.4）。"""

    permission_required = "attachments.change_attachment"

    def post(self, request, pk):
        attachment = get_object_or_404(Attachment, pk=pk)
        if not can_edit_attachment(request.user, attachment):
            raise PermissionDenied

        form = AttachmentMemoUpdateForm(request.POST, instance=attachment)
        if form.is_valid():
            form.save()
            messages.success(request, "添付メモを更新しました。")
        else:
            messages.error(request, "メモの更新に失敗しました。")

        if attachment.deal_id:
            return redirect("deals:deal_detail", pk=attachment.deal_id)
        elif attachment.activity_id:
            return redirect("activities:activity_detail", pk=attachment.activity_id)
        return redirect("home")
