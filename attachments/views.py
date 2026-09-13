from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from actionlogs.models import ActionLog
from activities.models import Activity
from activities.permissions import can_edit_activity
from attachments.forms import (
    BLOCKED_EXTENSIONS,
    MAX_ATTACHMENT_SIZE,
    AttachmentMemoUpdateForm,
    AttachmentUploadForm,
)
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

        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        if not files:
            messages.error(request, "ファイルが選択されていません。")
            return redirect(redirect_url)

        memo = request.POST.get("memo", "").strip()

        # 各ファイルの検証
        errors = []
        for f in files:
            if f.size > MAX_ATTACHMENT_SIZE:
                errors.append(f"「{f.name}」: ファイルサイズは15MB以下にしてください。")
            ext = Path(f.name).suffix.lower()
            if ext in BLOCKED_EXTENSIONS:
                errors.append(f"「{f.name}」: このファイル形式はセキュリティ上の理由によりアップロードできません。")

        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect(redirect_url)

        created_attachments = []
        try:
            with transaction.atomic():
                for f in files:
                    attachment = Attachment(
                        file=f,
                        memo=memo,
                        uploaded_by=request.user,
                        original_filename=f.name,
                    )
                    if deal:
                        attachment.deal = deal
                    if activity:
                        attachment.activity = activity
                    attachment.save()
                    created_attachments.append(attachment)

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
        except Exception as e:
            messages.error(request, f"ファイルのアップロード中にエラーが発生しました: {e}")
            return redirect(redirect_url)

        if len(created_attachments) == 1:
            messages.success(request, "ファイルをアップロードしました。")
        else:
            messages.success(request, f"{len(created_attachments)}件のファイルをアップロードしました。")

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

        is_ajax = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or request.headers.get("Accept") == "application/json"
            or request.content_type == "application/json"
        )

        form = AttachmentMemoUpdateForm(request.POST, instance=attachment)
        if form.is_valid():
            form.save()
            if is_ajax:
                return JsonResponse({"status": "success", "memo": attachment.memo})
            messages.success(request, "添付メモを更新しました。")
        else:
            if is_ajax:
                return JsonResponse({"status": "error", "errors": form.errors.get_json_data()}, status=400)
            messages.error(request, "メモの更新に失敗しました。")

        if attachment.deal_id:
            return redirect("deals:deal_detail", pk=attachment.deal_id)
        elif attachment.activity_id:
            return redirect("activities:activity_detail", pk=attachment.activity_id)
        return redirect("home")

