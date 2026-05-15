from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from persons.models import Person

from .models import CustomUser
from .services import link_user_to_person, unlink_user_from_person


class LinkUserPersonView(LoginRequiredMixin, View):
    """User と Person を紐付ける View（仕様書 §12.7）。

    権限: 本人（request.user == target_user）または accounts.link_user_to_person Permission。
    POST のみ受け付ける。
    """

    def post(self, request, user_id, person_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        person = get_object_or_404(Person, pk=person_id)

        if request.user != target_user and not request.user.has_perm(
            "accounts.link_user_to_person"
        ):
            raise PermissionDenied("紐付けの権限がありません")

        try:
            link_user_to_person(
                operator=request.user, user=target_user, person=person
            )
            messages.success(request, "紐付けました")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("home")


class UnlinkUserPersonView(LoginRequiredMixin, View):
    """User と Person の紐付けを解除する View（仕様書 §12.7）。

    権限: 本人または accounts.link_user_to_person Permission。POST のみ。
    """

    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)

        if request.user != target_user and not request.user.has_perm(
            "accounts.link_user_to_person"
        ):
            raise PermissionDenied("紐付け解除の権限がありません")

        try:
            unlink_user_from_person(operator=request.user, user=target_user)
            messages.success(request, "紐付けを解除しました")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("home")
