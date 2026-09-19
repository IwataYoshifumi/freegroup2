import json

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction

from accounts.models import Department, UserGroup
from actionlogs.constants import ACCESS_LIST_CREATED, ACCESS_LIST_UPDATED
from actionlogs.models import ActionLog
from permissions.models import AccessList, ACLEntry
from permissions.services import AccessListService

User = get_user_model()


class AccessListForm(forms.ModelForm):
    """アクセスリスト新規作成・編集用インライン一括フォーム（旧FG準拠）。"""

    class Meta:
        model = AccessList
        fields = ["name", "description"]
        labels = {
            "name": "アクセスリスト名",
            "description": "説明/備考",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "app-input",
                    "placeholder": "アクセスリスト名を入力",
                    "style": "max-width: 480px;",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "app-textarea app-input",
                    "rows": 3,
                    "placeholder": "説明・備考を入力（任意）",
                    "style": "max-width: 640px;",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        raw_json = self.data.get("entries_json")
        parsed_entries = []

        if raw_json:
            try:
                parsed_entries = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                self.add_error(None, "ロール設定データが不正な形式です。")
        elif "entry_target_type" in self.data:
            target_types = self.data.getlist("entry_target_type")
            perm_levels = self.data.getlist("entry_permission_level")
            target_ids = self.data.getlist("entry_target_id")
            entry_ids = self.data.getlist("entry_id")
            for i in range(len(target_types)):
                parsed_entries.append({
                    "order": i + 1,
                    "permission_level": perm_levels[i] if i < len(perm_levels) else "viewer",
                    "target_type": target_types[i],
                    "target_id": target_ids[i] if i < len(target_ids) else "",
                    "id": entry_ids[i] if i < len(entry_ids) else None,
                })

        User = get_user_model()
        valid_perm_levels = {choice[0] for choice in ACLEntry.PermissionLevel.choices}
        valid_entries = []

        for idx, entry in enumerate(parsed_entries, start=1):
            perm = entry.get("permission_level")
            ttype = entry.get("target_type")
            tid = str(entry.get("target_id") or "").strip()
            eid = entry.get("id")

            if not tid:
                # 対象が未選択の空行は無視
                continue

            if perm not in valid_perm_levels:
                self.add_error(None, f"行 {idx}: 不正な権限レベル「{perm}」です。")
                continue

            if ttype == "user":
                user = User.objects.filter(pk=tid, is_active=True).first()
                if not user:
                    self.add_error(None, f"行 {idx}: 指定されたユーザーが存在しません。")
                    continue
                ct = ContentType.objects.get_for_model(User)
            elif ttype == "department":
                dept = Department.objects.filter(pk=tid).first()
                if not dept:
                    self.add_error(None, f"行 {idx}: 指定された部署が存在しません。")
                    continue
                ct = ContentType.objects.get_for_model(Department)
            elif ttype == "user_group":
                ug = UserGroup.objects.filter(pk=tid).first()
                if not ug:
                    self.add_error(None, f"行 {idx}: 指定されたユーザーグループが存在しません。")
                    continue
                ct = ContentType.objects.get_for_model(UserGroup)
            else:
                self.add_error(None, f"行 {idx}: 不正な対象種別「{ttype}」です。")
                continue

            valid_entries.append({
                "order": idx,
                "permission_level": perm,
                "target_content_type": ct,
                "target_object_id": tid,
                "entry_id": eid,
            })

        cleaned_data["entries"] = valid_entries
        return cleaned_data

    def save(self, commit=True, user=None):
        is_new = self.instance._state.adding
        if is_new and user and not getattr(self.instance, "created_by_id", None):
            self.instance.created_by = user

        with transaction.atomic():
            instance = super().save(commit=commit)
            if commit:
                self._save_entries(instance, user=user, is_new=is_new)
            return instance

    def _save_entries(self, access_list, user=None, is_new=False):
        """エントリの一括作成・更新・削除およびキャッシュ即時再構築 (§2.3.1)。"""
        entries_data = self.cleaned_data.get("entries", [])
        existing_entries = {str(e.pk): e for e in access_list.entries.all()}
        kept_ids = set()

        for idx, item in enumerate(entries_data, start=1):
            eid = str(item.get("entry_id") or "")
            if eid in existing_entries:
                entry = existing_entries[eid]
                entry.order = idx
                entry.permission_level = item["permission_level"]
                entry.target_content_type = item["target_content_type"]
                entry.target_object_id = item["target_object_id"]
                entry.save()
                kept_ids.add(eid)
            else:
                new_entry = ACLEntry.objects.create(
                    access_list=access_list,
                    order=idx,
                    permission_level=item["permission_level"],
                    target_content_type=item["target_content_type"],
                    target_object_id=item["target_object_id"],
                )
                kept_ids.add(str(new_entry.pk))

        # 削除されたエントリの削除
        for old_id, old_entry in existing_entries.items():
            if old_id not in kept_ids:
                old_entry.delete()

        # キャッシュの即時完全再構築
        AccessListService.rebuild_for_access_list(access_list)

        # ActionLog の記録
        action = ACCESS_LIST_CREATED if is_new else ACCESS_LIST_UPDATED
        ActionLog.record(
            user=user,
            action=action,
            content_object=access_list,
            object_repr=str(access_list),
        )



class ACLEntryForm(forms.ModelForm):
    """アクセスリストエントリ追加・編集用フォーム（仕様書 v1.6 §2.3.1）。"""

    TARGET_TYPE_CHOICES = [
        ("user", "ユーザー"),
        ("department", "部署"),
        ("user_group", "ユーザーグループ"),
    ]

    target_type = forms.ChoiceField(
        choices=TARGET_TYPE_CHOICES,
        label="対象種別",
        widget=forms.Select(attrs={"class": "app-select app-input", "id": "id_target_type"}),
    )
    user_target = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by("username"),
        required=False,
        label="対象ユーザー",
        widget=forms.Select(attrs={"class": "app-select app-input", "id": "id_user_target"}),
    )
    department_target = forms.ModelChoiceField(
        queryset=Department.objects.all().order_by("name"),
        required=False,
        label="対象部署",
        widget=forms.Select(attrs={"class": "app-select app-input", "id": "id_department_target"}),
    )
    user_group_target = forms.ModelChoiceField(
        queryset=UserGroup.objects.all().order_by("name"),
        required=False,
        label="対象ユーザーグループ",
        widget=forms.Select(attrs={"class": "app-select app-input", "id": "id_user_group_target"}),
    )

    class Meta:
        model = ACLEntry
        fields = ["order", "permission_level"]
        labels = {
            "order": "並び順",
            "permission_level": "権限レベル",
        }
        widgets = {
            "order": forms.NumberInput(attrs={"class": "app-input", "min": 1}),
            "permission_level": forms.Select(attrs={"class": "app-select app-input"}),
        }

    def __init__(self, *args, access_list=None, **kwargs):
        self.access_list = access_list
        super().__init__(*args, **kwargs)

        if not self.instance.pk and access_list:
            max_order = access_list.entries.aggregate(models.Max("order"))["order__max"] or 0
            self.fields["order"].initial = max_order + 1

        if self.instance.pk and getattr(self.instance, "target_content_type_id", None):
            model_name = self.instance.target_content_type.model.lower()
            if model_name == "customuser":
                self.fields["target_type"].initial = "user"
                try:
                    self.fields["user_target"].initial = int(self.instance.target_object_id)
                except (ValueError, TypeError):
                    self.fields["user_target"].initial = self.instance.target_object_id
            elif model_name == "department":
                self.fields["target_type"].initial = "department"
                self.fields["department_target"].initial = self.instance.target_object_id
            elif model_name == "usergroup":
                self.fields["target_type"].initial = "user_group"
                self.fields["user_group_target"].initial = self.instance.target_object_id

    def clean(self):
        cleaned_data = super().clean()
        target_type = cleaned_data.get("target_type")

        if target_type == "user":
            user = cleaned_data.get("user_target")
            if not user:
                self.add_error("user_target", "対象ユーザーを選択してください。")
            else:
                cleaned_data["target_ct"] = ContentType.objects.get_for_model(user)
                cleaned_data["target_id"] = str(user.pk)
        elif target_type == "department":
            dept = cleaned_data.get("department_target")
            if not dept:
                self.add_error("department_target", "対象部署を選択してください。")
            else:
                cleaned_data["target_ct"] = ContentType.objects.get_for_model(dept)
                cleaned_data["target_id"] = str(dept.pk)
        elif target_type == "user_group":
            ug = cleaned_data.get("user_group_target")
            if not ug:
                self.add_error("user_group_target", "対象ユーザーグループを選択してください。")
            else:
                cleaned_data["target_ct"] = ContentType.objects.get_for_model(ug)
                cleaned_data["target_id"] = str(ug.pk)
        else:
            self.add_error("target_type", "有効な対象種別を選択してください。")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.access_list:
            instance.access_list = self.access_list
        if "target_ct" in self.cleaned_data:
            instance.target_content_type = self.cleaned_data["target_ct"]
        if "target_id" in self.cleaned_data:
            instance.target_object_id = self.cleaned_data["target_id"]
        if commit:
            instance.save()
        return instance
