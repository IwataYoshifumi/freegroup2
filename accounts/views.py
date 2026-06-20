import urllib.parse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView

from back_navigator.back_navigator import BackNavigator
from persons.models import Person

from .constants import ADMIN_ROLE_CODE
from .forms import RoleCreateForm, RoleUpdateForm
from .models import CustomUser, Role
from .services import (
    assign_role_to_user,
    create_role,
    delete_role,
    is_self_link_email_match,
    link_user_to_person,
    retire_user,
    role_holder_count,
    self_link_alert_context,
    unlink_user_from_person,
    update_role,
)


class ProfileView(LoginRequiredMixin, View):
    """ログインユーザ自身のプロフィール画面（仕様書 §12.8）。"""

    template_name = "accounts/profile.html"

    def get(self, request):
        # 確認画面リンクに back_stack を引き継げるよう BackNavigator を渡す（push_current は呼ばない＝
        # PersonDetailView 同型。プロフィールはルート画面でクエリ状態を持たないため push しない）。
        ctx = {"back": BackNavigator(request)}
        # 未紐付け時はホームと同一基準で候補状態を算出し、淡い青カード内の共通 partial で出し分ける。
        # profile は 0 件状態（名刺アップロード誘導）も出すため show_no_candidate=True で include する。
        if request.user.person is None:
            ctx.update(self_link_alert_context(request.user))
        return render(request, self.template_name, ctx)


class LinkUserPersonConfirmView(LoginRequiredMixin, View):
    """User-Person 紐付け確認画面。GET: 確認表示 / POST: 紐付けまたは解除を実行。

    [性質] GET=準関数（DB読み取りのみ）/ POST=副作用あり（DB書込）
    [権限] LoginRequiredMixin のみ（本フローは常に request.user 自身への操作）
    """

    template_name = "accounts/link_user_person_confirm.html"

    def get(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        # 本フローは常に本人フロー（operator == user == request.user）。
        # メール不一致なら確認画面で link ボタンを出さず、理由を表示する（GET 時点判定）。
        email_match = is_self_link_email_match(request.user, person)
        # 出口も入口（active 限定）と揃える：非active Person には link ボタンを出さない。
        person_active = person.status == Person.Status.ACTIVE
        return render(request, self.template_name, {
            "person": person,
            "email_match": email_match,
            "person_active": person_active,
            "back": BackNavigator(request),
        })

    def post(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        action = request.POST.get("action")
        try:
            if action == "link":
                link_user_to_person(operator=request.user, user=request.user, person=person)
                messages.success(request, "紐付けました")
            elif action == "unlink":
                unlink_user_from_person(operator=request.user, user=request.user)
                messages.success(request, "紐付けを解除しました")
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect("accounts:link_user_person_confirm", person_id=person_id)
        return redirect("accounts:profile")


class StartLinkFlowView(LoginRequiredMixin, View):
    """「Person を探して紐付ける」フロー開始 View。

    [性質] 副作用なし（messages.info + redirect のみ）
    [権限] LoginRequiredMixin（本人フロー開始のみ）
    """

    def get(self, request):
        messages.info(
            request,
            "紐付け対象の Person を表示しています。リストから自分の Person を選んでください。",
        )
        params = urllib.parse.urlencode([
            ("email", request.user.email),
            ("searched", "1"),
            ("status", "active"),
        ])
        return redirect(f"{reverse('persons:person_list')}?{params}")


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


class UserListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """ユーザ管理一覧 View（退職者表示切替・キーワード検索対応）。"""

    model = CustomUser
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    permission_required = "accounts.retire_user"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.GET.get("show_retired") != "1":
            qs = qs.filter(is_active=True)

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
                | Q(person__primary_contact__full_name__icontains=q)
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:user_list"
        return context


class UserDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """ユーザ詳細 View。"""

    model = CustomUser
    template_name = "accounts/user_detail.html"
    pk_url_kwarg = "user_id"
    permission_required = "accounts.retire_user"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:user_list"
        return context


class RetireUserView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """退職処理の専用 View（管理者業務 UI、仕様書 §12.6 B）。

    GET: 後継者選択フォーム表示
    POST: retire_user() 実行 → ユーザ管理一覧にリダイレクト
    """

    permission_required = "accounts.retire_user"
    template_name = "accounts/retire_user.html"

    def get(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        successor_choices = CustomUser.objects.filter(is_active=True).exclude(pk=user_id)
        return render(request, self.template_name, {
            "target_user": target_user,
            "successor_choices": successor_choices,
            "active_app": "user_mgmt",
            "active_menu": "accounts:user_list",
        })

    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        successor_id = request.POST.get("successor")
        successor = get_object_or_404(CustomUser, pk=successor_id)
        try:
            retire_user(user=target_user, successor=successor)
            messages.success(request, f"{target_user.username} の退職処理を完了しました")
        except Exception as e:
            messages.error(request, f"退職処理に失敗しました: {e}")
        return redirect("accounts:user_list")


class AssignRoleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ロール割り当ての専用 View（管理者業務 UI、宿題F・ロール割り当て UI）。

    GET: 対象ユーザと is_active な Role 選択肢を出す確認画面を表示
    POST: 選択された role を services.assign_role_to_user で適用 → user_detail へリダイレクト

    自爆防止：対象 == ログインユーザ本人のロール変更は GET/POST とも禁止する
    （admin が自分を誤って降格すると admin 不在で詰むため）。
    """

    permission_required = "accounts.assign_role_to_user"
    template_name = "accounts/assign_role.html"

    def _is_self(self, request, target_user):
        """[性質] 純関数（pk 比較のみ）／対象が操作者本人か判定する。"""
        return target_user.pk == request.user.pk

    def _safe_next(self, request, candidate):
        """[性質] 純関数（同一ホスト内の安全な戻り先 URL を返す）。

        オープンリダイレクト防止のため url_has_allowed_host_and_scheme で検証し、
        不正なら None を返す。
        """
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return candidate
        return None

    def _is_admin_target(self, target_user):
        """[性質] 準関数（target_user.role 参照のみ）／対象の現ロールが admin か判定する。"""
        return target_user.role is not None and target_user.role.code == ADMIN_ROLE_CODE

    def get(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        if self._is_self(request, target_user):
            messages.error(request, "自分自身のロールは変更できません")
            return redirect("accounts:user_detail", user_id=user_id)
        if self._is_admin_target(target_user):
            messages.error(
                request, "管理者ロールのユーザのロールは本体画面から変更できません"
            )
            return redirect("accounts:user_detail", user_id=user_id)
        # 変更先に admin は選ばせない（昇格は Django 管理サイトで）。
        roles = (
            Role.objects.filter(is_active=True)
            .exclude(code=ADMIN_ROLE_CODE)
            .order_by("sort_order")
        )
        return render(request, self.template_name, {
            "target_user": target_user,
            "roles": roles,
            "next": self._safe_next(request, request.GET.get("next")) or "",
            "active_app": "user_mgmt",
            "active_menu": "accounts:user_list",
        })

    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        if self._is_self(request, target_user):
            messages.error(request, "自分自身のロールは変更できません")
            return redirect("accounts:user_detail", user_id=user_id)

        role_id = request.POST.get("role")
        role = get_object_or_404(Role, pk=role_id, is_active=True)
        try:
            changed = assign_role_to_user(
                operator=request.user, user=target_user, role=role
            )
        except ValidationError as e:
            # admin 両方向ガード（最終砦）に弾かれた場合など。
            messages.error(request, e.message if hasattr(e, "message") else str(e))
            return redirect("accounts:user_detail", user_id=user_id)
        if changed:
            messages.success(
                request, f"{target_user.username} のロールを「{role}」に変更しました"
            )
        else:
            messages.info(request, "ロールに変更はありません")
        next_url = self._safe_next(request, request.POST.get("next"))
        if next_url:
            return redirect(next_url)
        return redirect("accounts:user_detail", user_id=user_id)


# ---------------------------------------------------------------------------
# 業務ロール（Role）CRUD（アクセス管理UI (B)）
# ---------------------------------------------------------------------------


class RoleListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """業務ロール一覧 View。admin ロールは read-only（編集/削除導線を出さない）。"""

    model = Role
    template_name = "accounts/role_list.html"
    context_object_name = "roles"
    permission_required = "accounts.manage_role"

    def get_queryset(self):
        # num_holders は退職者も含む（user.role 全件）。
        return (
            Role.objects.annotate(num_holders=Count("users"))
            .order_by("sort_order", "name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["admin_role_code"] = ADMIN_ROLE_CODE
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:role_list"
        return context


class RoleCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """業務ロール新規作成 View。code は name から自動採番（Service 経由）。"""

    permission_required = "accounts.manage_role"
    template_name = "accounts/role_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": RoleCreateForm(),
            "mode": "create",
            "active_app": "user_mgmt",
            "active_menu": "accounts:role_list",
        })

    def post(self, request):
        form = RoleCreateForm(request.POST)
        if form.is_valid():
            try:
                role = create_role(
                    operator=request.user,
                    name=form.cleaned_data["name"],
                    sort_order=form.cleaned_data.get("sort_order") or 0,
                    memo=form.cleaned_data.get("memo") or "",
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return render(request, self.template_name, {
                    "form": form, "mode": "create",
                    "active_app": "user_mgmt", "active_menu": "accounts:role_list",
                })
            messages.success(request, f"ロール「{role.name}」を作成しました")
            return redirect("accounts:role_list")
        return render(request, self.template_name, {"form": form, "mode": "create"})


class RoleUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """業務ロール編集 View（表示名 ＋ default_groups を集約）。admin ロールは編集不可。"""

    permission_required = "accounts.manage_role"
    template_name = "accounts/role_form.html"

    def _reject_admin(self, request, role):
        """[性質] 準関数／admin ロールなら一覧へ戻すリダイレクトを返す（編集不可）。"""
        if role.code == ADMIN_ROLE_CODE:
            messages.error(request, "管理者ロールは編集できません")
            return redirect("accounts:role_list")
        return None

    def get(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        rejected = self._reject_admin(request, role)
        if rejected:
            return rejected
        return render(request, self.template_name, {
            "form": RoleUpdateForm(instance=role),
            "mode": "edit",
            "role": role,
            "holder_count": role_holder_count(role),
            "active_app": "user_mgmt",
            "active_menu": "accounts:role_list",
        })

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        rejected = self._reject_admin(request, role)
        if rejected:
            return rejected
        form = RoleUpdateForm(request.POST, instance=role)
        if form.is_valid():
            try:
                result = update_role(
                    operator=request.user,
                    role=role,
                    name=form.cleaned_data["name"],
                    default_groups=list(form.cleaned_data["default_groups"]),
                    sort_order=form.cleaned_data.get("sort_order"),
                    memo=form.cleaned_data.get("memo"),
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return render(request, self.template_name, {
                    "form": form, "mode": "edit", "role": role,
                    "holder_count": role_holder_count(role),
                    "active_app": "user_mgmt", "active_menu": "accounts:role_list",
                })
            if result["groups_changed"]:
                messages.success(
                    request,
                    f"ロール「{role.name}」を更新しました。"
                    f"default_groups の変更を {result['affected_count']} 人（退職者含む）に反映しました。"
                )
            else:
                messages.success(request, f"ロール「{role.name}」を更新しました")
            return redirect("accounts:role_list")
        return render(request, self.template_name, {
            "form": form, "mode": "edit", "role": role,
            "holder_count": role_holder_count(role),
            "active_app": "user_mgmt", "active_menu": "accounts:role_list",
        })


class RoleDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """業務ロール削除 View。保持者（退職者含む）がいれば削除不可。admin ロールも不可。"""

    permission_required = "accounts.manage_role"
    template_name = "accounts/role_confirm_delete.html"

    def _reject_admin(self, request, role):
        if role.code == ADMIN_ROLE_CODE:
            messages.error(request, "管理者ロールは削除できません")
            return redirect("accounts:role_list")
        return None

    def get(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        rejected = self._reject_admin(request, role)
        if rejected:
            return rejected
        holders = role.users.all().order_by("-is_active", "username")
        return render(request, self.template_name, {
            "role": role,
            "holders": holders,
            "holder_count": holders.count(),
            "active_app": "user_mgmt",
            "active_menu": "accounts:role_list",
        })

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        rejected = self._reject_admin(request, role)
        if rejected:
            return rejected
        try:
            delete_role(operator=request.user, role=role)
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, "message") else str(e))
            return redirect("accounts:role_delete", pk=pk)
        messages.success(request, "ロールを削除しました")
        return redirect("accounts:role_list")


# ---------------------------------------------------------------------------
# 権限グループ（auth.Group）閲覧専用ビュー（アクセス管理UI (C-2)）
# ---------------------------------------------------------------------------

# 閲覧時に隠す Django フレームワーク app（CRUD permission のノイズを落とす）。
FRAMEWORK_APP_LABELS = {"admin", "auth", "contenttypes", "sessions"}


def _is_admin_group(group):
    """[性質] 純関数（グループ名判定のみ）／命名規約上 admin 系グループか判定する。

    admin 系は `*_admin`（campaign_admin / user_admin 等）で、末尾一致で全件判定できる。
    """
    return group.name.endswith("_admin")


def _permission_section_label(content_type):
    """[性質] 純関数／permission のグルーピング見出し（app / モデル）を返す。"""
    return f"{content_type.app_label} / {content_type.model}"


def group_permissions_by_section(permissions):
    """[性質] 純関数（与えられた Permission 群を app/モデル単位でグルーピングする・DB操作なし）。

    フレームワーク app（FRAMEWORK_APP_LABELS）由来は除外する。グループ詳細(C-2)と
    パーミッション一覧(D)で除外リスト・グルーピングを二重定義しないための共通処理。
    [入力] permissions: content_type を解決済み（select_related 推奨）の Permission iterable
    [出力] (sections: dict[str -> list[Permission]], hidden_count: int)
    """
    sections = {}
    hidden_count = 0
    for p in permissions:
        if p.content_type.app_label in FRAMEWORK_APP_LABELS:
            hidden_count += 1
            continue
        sections.setdefault(_permission_section_label(p.content_type), []).append(p)
    return sections, hidden_count


class GroupListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """権限グループ一覧（閲覧専用）。グループ名・保持 permission 数・紐づく Role・admin 系表示。"""

    model = Group
    template_name = "accounts/group_list.html"
    context_object_name = "groups"
    permission_required = "accounts.manage_role"

    def get_queryset(self):
        return (
            Group.objects.annotate(num_perms=Count("permissions"))
            .order_by("name")
            .prefetch_related("default_for_roles")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["groups_info"] = [
            {
                "group": g,
                "num_perms": g.num_perms,
                "roles": list(g.default_for_roles.all()),
                "is_admin": _is_admin_group(g),
            }
            for g in context["groups"]
        ]
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:group_list"
        return context


class GroupDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """権限グループ詳細（閲覧専用）。保持 permission を app/モデル単位でグルーピング表示。

    フレームワーク app（admin/auth/contenttypes/sessions）由来の CRUD permission は
    閲覧の見やすさ優先で除外する（除外件数は画面に明示）。表示は素のまま（codename と
    既存 name を併記・日本語化変換はしない）。
    """

    model = Group
    template_name = "accounts/group_detail.html"
    context_object_name = "group"
    permission_required = "accounts.manage_role"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        perms = (
            self.object.permissions.select_related("content_type")
            .order_by("content_type__app_label", "content_type__model", "codename")
        )
        sections, hidden_count = group_permissions_by_section(perms)
        context["perm_sections"] = sections
        context["shown_count"] = sum(len(v) for v in sections.values())
        context["hidden_count"] = hidden_count
        context["is_admin_group"] = _is_admin_group(self.object)
        context["roles"] = list(self.object.default_for_roles.all())
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:group_list"
        return context


class PermissionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """パーミッション一覧（閲覧専用）。permission→それを含むグループの逆引きを見せる。

    (C-2) グループ詳細（グループ→permission の順引き）と対になる逆引きビュー。表示範囲は
    (C-2) と同一（ドメイン app に絞り・フレームワーク app 除外・app/モデル単位グルーピング）で、
    group_permissions_by_section に共通化している。codename / name は素のまま（日本語化なし）。
    """

    model = Permission
    template_name = "accounts/permission_list.html"
    context_object_name = "permissions"
    permission_required = "accounts.manage_role"

    def get_queryset(self):
        qs = (
            Permission.objects.select_related("content_type")
            .exclude(content_type__app_label__in=FRAMEWORK_APP_LABELS)
            .prefetch_related("group_set")
            .order_by("content_type__app_label", "content_type__model", "codename")
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(codename__icontains=q)
                | Q(name__icontains=q)
                | Q(content_type__app_label__icontains=q)
                | Q(content_type__model__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # ドメイン app に絞り済みなので hidden は捨てる。グルーピングは (C-2) と共通。
        sections, _ = group_permissions_by_section(context["permissions"])
        context["perm_sections"] = sections
        context["q"] = self.request.GET.get("q", "")
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:permission_list"
        return context
