"""full_name 必須化（v1.7、手動作成経路の空保存防止）のテスト。

ContactCreateForm の clean に追加した _require_full_name の検証。
- 空文字 / 空白のみ の full_name を ContactCreateForm が弾く（full_name エラー）。
- full_name が入っていれば従来どおり valid（正常系が壊れない）。
- salutation_name 必須も引き続き効く（回帰なし）。
- 適用範囲が ContactCreateForm 限定で、Update / UpdateActive / AddAdditionalRole（fix・別肩書）
  には副作用が無いこと（これらは full_name 空でも full_name エラーにならない）。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from contacts.forms import (
    ContactAddAdditionalRoleForm,
    ContactCreateForm,
    ContactUpdateActiveForm,
    ContactUpdateForm,
)
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _create_data(**overrides):
    """ContactCreateForm 用 POST data（salutation 充足、full_name は override 可）。"""
    data = {f: "" for f in Contact.UPDATABLE_FIELDS}
    data["salutation_name"] = "宛名 様"
    data["full_name"] = "山田太郎"
    data.update(overrides)
    return data


class FullNameRequiredCreateTests(TestCase):
    """ContactCreateForm（手動作成）で full_name 必須が効く。"""

    def test_empty_full_name_invalid(self):
        form = ContactCreateForm(data=_create_data(full_name=""))
        self.assertFalse(form.is_valid())
        self.assertIn("full_name", form.errors)

    def test_whitespace_only_full_name_invalid(self):
        form = ContactCreateForm(data=_create_data(full_name="   "))
        self.assertFalse(form.is_valid())
        self.assertIn("full_name", form.errors)

    def test_fullwidth_space_only_full_name_invalid(self):
        # 全角スペースのみも空扱い（CharField strip + _require_full_name の strip）。
        form = ContactCreateForm(data=_create_data(full_name="　　"))
        self.assertFalse(form.is_valid())
        self.assertIn("full_name", form.errors)

    def test_filled_full_name_valid(self):
        form = ContactCreateForm(data=_create_data(full_name="新規太郎"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["full_name"], "新規太郎")

    def test_error_message_is_japanese(self):
        form = ContactCreateForm(data=_create_data(full_name=""))
        self.assertFalse(form.is_valid())
        self.assertIn("氏名（フルネーム）を入力してください。", form.errors["full_name"])

    def test_salutation_still_required_regression(self):
        # full_name はあるが salutation を空にする → salutation エラーは引き続き出る。
        form = ContactCreateForm(data=_create_data(salutation_name="   "))
        self.assertFalse(form.is_valid())
        self.assertIn("salutation_name", form.errors)


class FullNameGuardScopeTests(TestCase):
    """full_name 必須化は ContactCreateForm 限定。他フォームへ波及しない。"""

    def setUp(self):
        self.person = Person.objects.create(status=Person.Status.ACTIVE)
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="既存太郎",
        )
        self.active = Contact.objects.create(
            person=self.person,
            status=Contact.Status.ACTIVE,
            full_name="既存花子",
        )

    def _update_data(self, *, include_change_reason=True, **overrides):
        data = {f: getattr(self.primary, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        data["salutation_name"] = "宛名 様"
        if include_change_reason:
            data["change_reason"] = "fix"
        data.update(overrides)
        return data

    def test_update_form_empty_full_name_no_full_name_error(self):
        """12番 fix（ContactUpdateForm）は full_name 空でも full_name エラーを出さない（対象外）。"""
        form = ContactUpdateForm(
            data=self._update_data(full_name=""), target_contact=self.primary
        )
        form.is_valid()  # 検証を走らせる
        self.assertNotIn("full_name", form.errors)

    def test_update_active_form_empty_full_name_no_full_name_error(self):
        """13番（ContactUpdateActiveForm）も対象外。"""
        form = ContactUpdateActiveForm(
            data=self._update_data(include_change_reason=False, full_name=""),
            target_contact=self.active,
        )
        form.is_valid()
        self.assertNotIn("full_name", form.errors)

    def test_add_additional_role_form_empty_full_name_no_full_name_error(self):
        """9番別肩書追加（ContactAddAdditionalRoleForm）も対象外。"""
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["salutation_name"] = "別肩書 様"
        data["full_name"] = ""
        form = ContactAddAdditionalRoleForm(data=data, person=self.person)
        form.is_valid()
        self.assertNotIn("full_name", form.errors)
