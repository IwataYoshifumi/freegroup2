"""Phase D §4.4 / §4.5 のテスト。

手動 Form 経路の正規化通し（§3.4）・salutation_name 必須化（§3.5）・
salutation_name_is_manual の View 層自動セット（§3.6）を検証する。

§3.1〜§3.3（compute_salutation_name / compose_full_address / Contact.save() 配線）と
§4.1〜§4.3 のテストは既存（test_normalization.py / tests.py）にあるため本ファイルでは扱わない。
"""

from django import forms
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from contacts.forms import (
    ContactAddAdditionalRoleForm,
    ContactCreateForm,
    ContactUpdateActiveForm,
    ContactUpdateForm,
    build_contact_sns_formset,
)
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _grant_contact_perms(user):
    """Phase 7 段3-2：Contact 系 View に標準 CRUD 権限ガードが入ったため、View を叩く
    既存テストの正常系を保つよう view/add/change/delete_contact を一括付与する補正。

    宿題F（v1.7+）：Update 系フォーム経路にも owner ガード（can_edit_contact）が入った。
    既存テストの Contact は所有者未設定のため、横断権限 edit_all_contacts も併せて付与する。"""
    from django.contrib.auth.models import Permission

    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="contacts",
            codename__in=[
                "view_contact",
                "add_contact",
                "change_contact",
                "delete_contact",
                "edit_all_contacts",
            ],
        )
    )


def _empty_sns_management_form(prefix="sns"):
    """ContactSns InlineFormSet の空 management_form（View POST テスト用、Phase F1 §11.6.7）。"""
    return {
        f"{prefix}-TOTAL_FORMS": "0",
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


class ContactFormNormalizationTests(TestCase):
    """§3.4：Form の clean() が normalization 純関数を通すことの検証。"""

    def _create_data(self, **overrides):
        """ContactCreateForm 用 POST data（UPDATABLE_FIELDS 全埋め + salutation 必須）。"""
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["salutation_name"] = "山田 様"  # §3.5 必須化を満たす
        data["full_name"] = "山田太郎"  # v1.7 full_name 必須化を満たす（各テストで override 可）
        data["country"] = "JP"  # Phase D2：postal/rest は country 別正規化。JP として検証
        data.update(overrides)
        return data

    def test_create_form_normalizes_phone(self):
        form = ContactCreateForm(data=self._create_data(mobile_phone="０９０-1234-5678"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["mobile_phone"], "09012345678")

    def test_create_form_normalizes_postal_code(self):
        form = ContactCreateForm(data=self._create_data(postal_code="123-4567"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["postal_code"], "1234567")

    def test_create_form_normalizes_organization_abbrev(self):
        form = ContactCreateForm(data=self._create_data(organization="㈱テスト"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["organization"], "株式会社テスト")

    def test_create_form_normalizes_email_lowercase(self):
        form = ContactCreateForm(data=self._create_data(email="  TARO@Example.COM "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "taro@example.com")

    def test_create_form_normalizes_department_and_title(self):
        form = ContactCreateForm(
            data=self._create_data(department="営 業 部", title="部　長")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["department"], "営業部")
        self.assertEqual(form.cleaned_data["title"], "部長")

    def test_create_form_normalizes_full_name_removes_space(self):
        # normalize_full_name は空白を除去する（§11.9.5.1・既存 Phase B 挙動）。
        form = ContactCreateForm(data=self._create_data(full_name="山田 太郎"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["full_name"], "山田太郎")

    def test_create_form_whitespace_only_full_name_rejected(self):
        # v1.7：full_name 必須化により、空白のみ（CharField strip で空になる）は
        # ContactCreateForm で弾く（旧挙動の「空のまま valid」は空保存バグだったため変更）。
        form = ContactCreateForm(data=self._create_data(full_name="　 "))
        self.assertFalse(form.is_valid())
        self.assertIn("full_name", form.errors)

    def test_add_additional_role_form_normalizes(self):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "別肩書 次郎"
        data["salutation_name"] = "次郎 様"  # Phase F1 で 9 番も salutation 必須化
        data["mobile_phone"] = "０９０-0000-1111"
        form = ContactAddAdditionalRoleForm(data=data, person=person)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["full_name"], "別肩書次郎")
        self.assertEqual(form.cleaned_data["mobile_phone"], "09000001111")


class ContactFormSalutationRequiredTests(TestCase):
    """§3.5：salutation_name 必須化（Create / Update / UpdateActive の 3 Form）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="salutation_req_user", password="dummy"
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="既存太郎",
        )

    def _update_base_data(self, *, include_change_reason=True):
        data = {f: getattr(self.contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        if include_change_reason:
            data["change_reason"] = "fix"
        return data

    def test_create_form_blank_salutation_invalid(self):
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "新規太郎"
        data["salutation_name"] = "   "  # 空白のみ
        form = ContactCreateForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("salutation_name", form.errors)

    def test_create_form_with_salutation_valid(self):
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "新規太郎"
        data["salutation_name"] = "新規 様"
        form = ContactCreateForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_update_form_blank_salutation_invalid(self):
        data = self._update_base_data()
        data["salutation_name"] = ""
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("salutation_name", form.errors)

    def test_update_active_form_blank_salutation_invalid(self):
        data = self._update_base_data(include_change_reason=False)
        data["salutation_name"] = ""
        form = ContactUpdateActiveForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("salutation_name", form.errors)

    def test_add_additional_role_form_blank_salutation_invalid(self):
        """ContactAddAdditionalRoleForm も salutation 必須（Phase F1 で 9 番も対象化）。"""
        person = Person.objects.create(status=Person.Status.ACTIVE)
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "別肩書太郎"
        data["salutation_name"] = "   "  # 空白のみ → 必須エラー
        form = ContactAddAdditionalRoleForm(data=data, person=person)
        self.assertFalse(form.is_valid())
        self.assertIn("salutation_name", form.errors)

    def test_add_additional_role_form_with_salutation_valid(self):
        """salutation を埋めれば valid（Phase F1）。"""
        person = Person.objects.create(status=Person.Status.ACTIVE)
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "別肩書太郎"
        data["salutation_name"] = "別肩書 様"
        form = ContactAddAdditionalRoleForm(data=data, person=person)
        self.assertTrue(form.is_valid(), form.errors)


class SalutationIsManualViewTests(TestCase):
    """§3.6：salutation_name_is_manual の View 層自動セット（Form 経路）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="is_manual_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)

    def test_create_with_explicit_salutation_sets_manual_true(self):
        """新規作成で宛名を手動化（hidden フラグ=true 送信）→ is_manual=True、値が保持される。

        v1.7 コミット3/3：手動/自動は changed_data ではなく hidden フラグ
        （salutation_name_is_manual）で明示送信する（鉛筆 UI が立てる）。
        """
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "手動太郎"
        data["salutation_name"] = "手動 会長"  # 自動生成（手動太郎 様）と異なる明示値
        data["salutation_name_is_manual"] = "true"  # 鉛筆 UI 相当（手動化フラグ）
        data.update(_empty_sns_management_form())
        resp = self.client.post(reverse("contacts:contact_create"), data=data)
        self.assertEqual(resp.status_code, 302)
        contact = Contact.objects.get(full_name="手動太郎")
        self.assertTrue(contact.salutation_name_is_manual)
        self.assertEqual(contact.salutation_name, "手動 会長")

    def test_update_active_edit_salutation_sets_manual_true(self):
        """active Contact 修正で宛名を手動化（hidden フラグ=true）→ is_manual=True、値が保持される。"""
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.ACTIVE,
            full_name="渡辺一郎",
            lang="ja",
        )
        self.assertFalse(contact.salutation_name_is_manual)

        data = {f: getattr(contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        data["salutation_name"] = "渡辺 社長"  # 明示変更
        data["salutation_name_is_manual"] = "true"  # 鉛筆 UI 相当（手動化フラグ）
        data.update(_empty_sns_management_form())
        url = reverse("contacts:contact_update_active", kwargs={"pk": contact.pk})
        resp = self.client.post(url, data=data)
        self.assertEqual(resp.status_code, 302)

        contact.refresh_from_db()
        self.assertTrue(contact.salutation_name_is_manual)
        self.assertEqual(contact.salutation_name, "渡辺 社長")

    def test_update_active_change_last_name_recomputes_when_not_manual(self):
        """宛名を触らず姓だけ変更 → is_manual=False のまま、宛名は自動再計算される。"""
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.ACTIVE,
            full_name="田中花子",
            last_name="田中",
            lang="ja",
        )
        self.assertEqual(contact.salutation_name, "田中 様")

        data = {f: getattr(contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        # salutation_name は現在値のまま（変更しない）、姓系フィールドのみ変更
        data["last_name"] = "佐藤"
        data["full_name"] = "佐藤花子"
        data.update(_empty_sns_management_form())
        url = reverse("contacts:contact_update_active", kwargs={"pk": contact.pk})
        resp = self.client.post(url, data=data)
        self.assertEqual(resp.status_code, 302)

        contact.refresh_from_db()
        self.assertFalse(contact.salutation_name_is_manual)
        self.assertEqual(contact.salutation_name, "佐藤 様")


class ContactCreateTemplateRenderTests(TestCase):
    """§4.6 補助：create 画面に追加 8 フィールド・js- フック・address readonly が出ること。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tmpl_render_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)

    def test_create_page_renders_added_fields_and_hooks(self):
        resp = self.client.get(reverse("contacts:contact_create"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        # 追加 8 フィールドの input/select が描画される
        for fid in (
            # 前回追加 8 フィールド
            "id_other_name_parts",
            "id_name_order",
            "id_region",
            "id_city",
            "id_rest_of_address",
            "id_country",
            "id_org_phone",
            "id_org_fax",
            # 案①で追加 5 フィールド
            "id_display_name",
            "id_phonetic_name",
            "id_alias_name",
            "id_legal_entity_type",
            "id_legal_entity_type_position",
        ):
            self.assertIn(fid, html, fid)
        # §3.7 js- フックが氏名系 widget に付与される
        for cls in ("js-name-full", "js-name-last", "js-name-order"):
            self.assertIn(cls, html, cls)
        # Phase E：address は Form フィールドから除外（save() が自動 compose）。
        # かつて検証していた address readonly input は描画されなくなったため assert を削除。
        # country は JP デフォルト
        self.assertIn('value="JP"', html)

    def test_ui_improvements_labels_required_and_comment(self):
        resp = self.client.get(reverse("contacts:contact_create"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        # 提案B：ラベルに app-form__label クラスが付与される
        self.assertIn('class="app-form__label"', html)
        # 提案C：salutation_name に必須マーク（app-required ピル）が付く
        self.assertRegex(
            html,
            r'for="id_salutation_name">[^<]*<span class="app-required">必須</span>',
        )
        # BUG-1：複数行 Django コメントが {% comment %} 化され、生テキストが漏れない
        self.assertNotIn("InlineFormSet による編集 UI", html)


# NOTE: ComposeAddressFormTests（Phase D2）は Phase E で削除。
# 理由：Phase E で address を Form から外し、組み立てを ContactBaseForm._compose_address から
# Contact.save() へ移管したため、「Form が cleaned_data['address'] を compose する／address
# widget が readonly」という検証対象自体が消失した。同等カバレッジは contacts/tests.py の
# Contact.save() address compose テスト群と AJAX 正規化テスト群（GB postal 保護含む）が引き継ぐ。


class UpdatePreservesRenderedFieldsTests(TestCase):
    """§4.6：画面に追加した 8 フィールドが更新で空化されないことの回帰テスト。"""

    # Phase D 完了時点で UPDATABLE_FIELDS 全 32 フィールドが 3 テンプレートに描画される。
    # ブラウザはこれらを POST するため、全フィールドを現在値で送る状況を再現する。
    RENDERED_FIELDS = tuple(Contact.UPDATABLE_FIELDS)

    def setUp(self):
        self.user = User.objects.create_user(
            username="preserve_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)
        self.person = Person.objects.create(status=Person.Status.ACTIVE)
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.ACTIVE,
            full_name="保持太郎",
            last_name="保持",
            name_order="last_first",
            other_name_parts="ミドル",
            display_name="保持(営業)",
            phonetic_name="ホジタロウ",
            alias_name="旧姓田中",
            organization="旧会社",
            legal_entity_type="株式会社",
            legal_entity_type_position="Pre",
            country="JP",
            region="愛知県",
            city="豊田市",
            rest_of_address="1-2-3",
            org_phone="0565000000",
            org_fax="0565000001",
            lang="ja",
        )

    def test_changing_one_field_does_not_blank_added_fields(self):
        # ブラウザが描画フィールドを現在値で POST する状況を再現し、organization のみ変更。
        data = {f: getattr(self.contact, f) or "" for f in self.RENDERED_FIELDS}
        data["note"] = ""
        data["organization"] = "新会社"
        data.update(_empty_sns_management_form())
        url = reverse(
            "contacts:contact_update_active", kwargs={"pk": self.contact.pk}
        )
        resp = self.client.post(url, data=data)
        self.assertEqual(resp.status_code, 302)

        self.contact.refresh_from_db()
        self.assertEqual(self.contact.organization, "新会社")
        # §2.6：画面に追加した 8 フィールドが空化されていない
        self.assertEqual(self.contact.region, "愛知県")
        self.assertEqual(self.contact.city, "豊田市")
        self.assertEqual(self.contact.rest_of_address, "1-2-3")
        self.assertEqual(self.contact.name_order, "last_first")
        self.assertEqual(self.contact.other_name_parts, "ミドル")
        self.assertEqual(self.contact.country, "JP")
        self.assertEqual(self.contact.org_phone, "0565000000")
        self.assertEqual(self.contact.org_fax, "0565000001")
        # 案①で追加した 5 フィールドも空化されない（潜在バグ完全解消）。
        # v1.7：display_name は display_name_is_manual=False のとき full_name に自動追従する
        # （本ケースは display_name を手動編集していない＝changed_data に無いため非 manual のまま）。
        # 「空化しない」意図は維持され、値は full_name（保持太郎）になる。
        self.assertEqual(self.contact.display_name, "保持太郎")
        self.assertEqual(self.contact.phonetic_name, "ホジタロウ")
        self.assertEqual(self.contact.alias_name, "旧姓田中")
        self.assertEqual(self.contact.legal_entity_type, "株式会社")
        self.assertEqual(self.contact.legal_entity_type_position, "Pre")
        # §3.8：address は 4 要素から自動組み立てされる
        self.assertEqual(self.contact.address, "愛知県豊田市1-2-3")


class AddedFiveFieldsFormTests(TestCase):
    """案① / §4.4：追加 5 フィールドが Form clean を通ること（空・入力あり・不正値）。"""

    _FIVE = (
        "display_name",
        "phonetic_name",
        "alias_name",
        "legal_entity_type",
        "legal_entity_type_position",
    )

    def _create_data(self, **overrides):
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["salutation_name"] = "宛名 様"
        data["full_name"] = "氏名太郎"  # v1.7 full_name 必須化を満たす（各テストで override 可）
        data.update(overrides)
        return data

    def test_blank_five_fields_valid(self):
        form = ContactCreateForm(data=self._create_data())
        self.assertTrue(form.is_valid(), form.errors)
        for field in self._FIVE:
            self.assertEqual(form.cleaned_data[field], "")

    def test_filled_five_fields_pass_through(self):
        form = ContactCreateForm(
            data=self._create_data(
                display_name="表示名",
                phonetic_name="ヒョウジメイ",
                alias_name="旧姓田中",
                legal_entity_type="医療法人",
                legal_entity_type_position="Post",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["display_name"], "表示名")
        self.assertEqual(form.cleaned_data["phonetic_name"], "ヒョウジメイ")
        self.assertEqual(form.cleaned_data["alias_name"], "旧姓田中")
        self.assertEqual(form.cleaned_data["legal_entity_type"], "医療法人")
        self.assertEqual(form.cleaned_data["legal_entity_type_position"], "Post")

    def test_invalid_position_choice_rejected(self):
        # legal_entity_type_position は choices フィールド（Pre/Post/Mid/other）。範囲外は不正。
        form = ContactCreateForm(
            data=self._create_data(legal_entity_type_position="ZZZ")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("legal_entity_type_position", form.errors)


class PostalDisplayContactDetailTests(TestCase):
    """Phase D2 話2：contact_detail で postal_code が国別整形表示される（DB は raw 維持）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="postal_disp_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)

    def test_jp_postal_displayed_with_hyphen_db_stays_raw(self):
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name="郵便太郎",
            country="JP",
            postal_code="4710001",
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])

        resp = self.client.get(
            reverse("contacts:contact_detail", kwargs={"pk": contact.pk})
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        # 画面表示は国別整形（471-0001）
        self.assertIn("471-0001", html)
        # DB は raw のまま（整形は表示専用）
        contact.refresh_from_db()
        self.assertEqual(contact.postal_code, "4710001")


class EffectiveLangAndNameOrderTests(TestCase):
    """v1.7 コミット3/3 要素1・2：effective_lang の算出と name_order の hidden 確定値。"""

    def _contact(self, lang):
        p = Person.objects.create(status=Person.Status.ACTIVE)
        return Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            first_name="太郎",
            lang=lang,
        )

    def test_effective_lang_ja(self):
        f = ContactUpdateForm(target_contact=self._contact("ja"))
        self.assertEqual(f.effective_lang, "ja")

    def test_effective_lang_en(self):
        f = ContactUpdateForm(target_contact=self._contact("en"))
        self.assertEqual(f.effective_lang, "en")

    def test_effective_lang_empty_falls_back_to_settings_ja(self):
        # 空 lang → settings.LANGUAGE_CODE（"ja"）にフォールバック
        f = ContactUpdateForm(target_contact=self._contact(""))
        self.assertEqual(f.effective_lang, "ja")

    def test_effective_lang_zh_rounds_to_ja(self):
        # zh / und 等は ja に丸める（zh 専用 UI は作らない）
        f = ContactUpdateForm(target_contact=self._contact("zh"))
        self.assertEqual(f.effective_lang, "ja")

    def test_name_order_is_hidden(self):
        f = ContactUpdateForm(target_contact=self._contact("ja"))
        self.assertIsInstance(f.fields["name_order"].widget, forms.HiddenInput)

    def test_name_order_forced_last_first_for_ja(self):
        c = self._contact("ja")
        data = {fn: getattr(c, fn) or "" for fn in Contact.UPDATABLE_FIELDS}
        data["change_reason"] = "fix"
        data["name_order"] = "first_last"  # 改ざんしても effective_lang で上書きされる
        f = ContactUpdateForm(data=data, target_contact=c)
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["name_order"], "last_first")

    def test_name_order_forced_first_last_for_en(self):
        c = self._contact("en")
        data = {fn: getattr(c, fn) or "" for fn in Contact.UPDATABLE_FIELDS}
        data["change_reason"] = "fix"
        data["name_order"] = "last_first"
        f = ContactUpdateForm(data=data, target_contact=c)
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["name_order"], "first_last")


class NamePartOrderRenderTests(TestCase):
    """v1.7：氏名原本（姓 last_name / 名 first_name）入力欄の表示順を effective_lang で
    出し分ける（ja=姓→名 / en=名→姓）。表示順のみで役割・ラベルは不変。

    _contact_fields.html を実コンテキストで描画し、姓欄(id_last_name)と名欄(id_first_name)の
    出現順を検証する。派生（full_name 等）の js- 追従フックは順序非依存のため、ここでは
    並び順のみを担保する（追従ロジックは既存テストが担保）。
    """

    def _contact(self, lang):
        p = Person.objects.create(status=Person.Status.ACTIVE)
        return Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            first_name="太郎",
            lang=lang,
        )

    def _render(self, lang):
        c = self._contact(lang)
        form = ContactUpdateForm(target_contact=c)
        sns_formset = build_contact_sns_formset(instance=c)
        html = render_to_string(
            "contacts/_contact_fields.html",
            {"form": form, "sns_formset": sns_formset},
        )
        return html, html.index('id="id_last_name"'), html.index('id="id_first_name"')

    def test_ja_renders_last_name_before_first_name(self):
        """ja（既定）：姓 → 名 の順で出る。"""
        html, last_pos, first_pos = self._render("ja")
        self.assertIn('id="id_last_name"', html)
        self.assertIn('id="id_first_name"', html)
        self.assertLess(last_pos, first_pos, "ja は姓→名の順で描画されるべき")

    def test_en_renders_first_name_before_last_name(self):
        """en：名 → 姓 の順で出る（表示順のみ。役割・ラベルは不変）。"""
        html, last_pos, first_pos = self._render("en")
        self.assertIn('id="id_last_name"', html)
        self.assertIn('id="id_first_name"', html)
        self.assertLess(first_pos, last_pos, "en は名→姓の順で描画されるべき")

    def test_js_name_hooks_preserved_in_both_orders(self):
        """並べ替え後も原本→派生のライブ追従フック（js-name-last/js-name-first）が
        姓/名 widget に残る（追従の前提が壊れていない）。"""
        for lang in ("ja", "en"):
            html, _, _ = self._render(lang)
            self.assertIn("js-name-last", html)
            self.assertIn("js-name-first", html)


class LegalEntityTypeEnHiddenTests(TestCase):
    """v1.7：法人格(legal_entity_type)は en で画面非表示だが hidden で値を保持し、
    en 保存時に既存値が消えないことを担保する。

    フリガナ・法人格の位置（en で実質空）は現状の「丸ごと省略」のままで、本手当ては
    legal_entity_type のみ（en でも Inc./Ltd. 等の値を持ちうるため）。
    """

    def _contact(self, lang, legal="Inc."):
        p = Person.objects.create(status=Person.Status.ACTIVE)
        return Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name="John Smith",
            last_name="Smith",
            first_name="John",
            salutation_name="Mr. John Smith",
            organization="Acme",
            legal_entity_type=legal,
            lang=lang,
        )

    def _render(self, lang):
        c = self._contact(lang)
        form = ContactUpdateForm(target_contact=c)
        sns_formset = build_contact_sns_formset(instance=c)
        return render_to_string(
            "contacts/_contact_fields.html",
            {"form": form, "sns_formset": sns_formset},
        )

    def test_ja_renders_visible_legal_entity_type_field(self):
        """ja：法人格は従来どおり通常入力欄（ラベル可視・hidden ではない）。"""
        html = self._render("ja")
        self.assertIn(">法人格<", html)
        self.assertNotIn('type="hidden" name="legal_entity_type"', html)

    def test_en_renders_legal_entity_type_as_hidden(self):
        """en：法人格は画面ラベルを出さず、値を hidden で POST に保持する。"""
        html = self._render("en")
        self.assertNotIn(">法人格<", html)  # 画面ラベルは出ない
        self.assertIn('type="hidden" name="legal_entity_type"', html)
        self.assertIn('value="Inc."', html)  # 既存値が hidden に乗る（POST 保持の根拠）

    def test_en_save_preserves_legal_entity_type(self):
        """en 保存で legal_entity_type の既存値が維持される（hidden が POST に乗るため
        cleaned 実値→get_update_contact→fix で old==new となり上書きされない）。
        派生 legal_entity_type_code も type 値が保たれる結果、不正に空/再導出されない。"""
        c = self._contact("en", legal="Ltd.")
        code_before = c.legal_entity_type_code
        user = User.objects.create_user(username="le_en_user", password="x")
        # en ページは hidden で legal_entity_type を送る＝POST に値が乗る状況を再現。
        data = {fn: getattr(c, fn) or "" for fn in Contact.UPDATABLE_FIELDS}
        form = ContactUpdateActiveForm(data=data, target_contact=c)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["legal_entity_type"], "Ltd.")
        c.fix(form, user)
        c.refresh_from_db()
        self.assertEqual(c.legal_entity_type, "Ltd.")
        self.assertEqual(c.legal_entity_type_code, code_before)


class OtherNamePartsJaHiddenTests(TestCase):
    """v1.7：ミドルネーム等(other_name_parts)は ja で画面非表示だが as_hidden で値を保持し、
    ja 保存時に既存値が空で上書きされないことを担保する（法人格 en 隠蔽の ja 版・方向逆）。

    ja の氏名組み立ては「姓 名」で other 不使用のため、隠してもライブ追従は無傷。
    """

    def _contact(self, lang, other="ビン"):
        p = Person.objects.create(status=Person.Status.ACTIVE)
        return Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name="山田 太郎",
            last_name="山田",
            first_name="太郎",
            other_name_parts=other,
            salutation_name="山田 様",
            lang=lang,
        )

    def _render(self, lang):
        c = self._contact(lang)
        form = ContactUpdateForm(target_contact=c)
        sns_formset = build_contact_sns_formset(instance=c)
        return render_to_string(
            "contacts/_contact_fields.html",
            {"form": form, "sns_formset": sns_formset},
        )

    def test_en_renders_visible_other_name_parts_field(self):
        """en：ミドルネーム等は従来どおり通常入力欄（ラベル可視・hidden ではない）。"""
        html = self._render("en")
        self.assertIn(">ミドルネーム等<", html)
        self.assertNotIn('type="hidden" name="other_name_parts"', html)

    def test_ja_renders_other_name_parts_as_hidden(self):
        """ja：ミドルネーム等は画面ラベルを出さず、値を hidden で POST に保持する。"""
        html = self._render("ja")
        self.assertNotIn(">ミドルネーム等<", html)  # 画面ラベルは出ない
        self.assertIn('type="hidden" name="other_name_parts"', html)
        self.assertIn('value="ビン"', html)  # 既存値が hidden に乗る（POST 保持の根拠）

    def test_ja_save_preserves_other_name_parts(self):
        """ja 保存で other_name_parts の既存値が維持される（hidden が POST に乗るため
        cleaned 実値→get_update_contact→fix で old==new となり空上書きされない）。"""
        c = self._contact("ja", other="ビン")
        user = User.objects.create_user(username="onp_ja_user", password="x")
        # ja ページは as_hidden で other_name_parts を送る＝POST に値が乗る状況を再現。
        data = {fn: getattr(c, fn) or "" for fn in Contact.UPDATABLE_FIELDS}
        form = ContactUpdateActiveForm(data=data, target_contact=c)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["other_name_parts"], "ビン")
        c.fix(form, user)
        c.refresh_from_db()
        self.assertEqual(c.other_name_parts, "ビン")


class ManualFlagToggleViewTests(TestCase):
    """v1.7 コミット3/3 要素3：hidden フラグによる手動/自動/自動戻しの送信反映。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="manual_flag_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)

    def _active_contact(self, **kw):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        defaults = dict(
            status=Contact.Status.ACTIVE,
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            lang="ja",
        )
        defaults.update(kw)
        return Contact.objects.create(person=person, **defaults)

    def _post_active(self, contact, overrides):
        data = {fn: getattr(contact, fn) or "" for fn in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        data.update(overrides)
        data.update(_empty_sns_management_form())
        url = reverse("contacts:contact_update_active", kwargs={"pk": contact.pk})
        return self.client.post(url, data=data)

    def test_full_name_manual_flag_true_keeps_value(self):
        # hidden フラグ true → full_name 手動値を保持（原本変更でも上書きしない）
        c = self._active_contact(full_name="カスタム氏名", full_name_is_manual=True)
        resp = self._post_active(
            c,
            {
                "full_name": "カスタム氏名",
                "full_name_is_manual": "true",
                "last_name": "佐藤",  # 原本変更
            },
        )
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertTrue(c.full_name_is_manual)
        self.assertEqual(c.full_name, "カスタム氏名")

    def test_full_name_revert_to_auto_recomputes(self):
        # 「自動に戻す」（hidden=false）→ 手動値破棄・原本から自動再計算（source 不変でも revert で再計算）
        c = self._active_contact(full_name="カスタム氏名", full_name_is_manual=True)
        resp = self._post_active(
            c,
            {
                "full_name": "カスタム氏名",  # 値は据え置きで送るが revert で破棄される
                "full_name_is_manual": "",  # 自動に戻す
            },
        )
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertFalse(c.full_name_is_manual)
        self.assertEqual(c.full_name, "山田 太郎")

    def test_salutation_revert_to_auto_recomputes(self):
        # 宛名を「自動に戻す」→ 手動値破棄・lang から自動再計算（ja＝姓＋様）
        c = self._active_contact(
            full_name="山田太郎",
            salutation_name="特別な宛名",
            salutation_name_is_manual=True,
        )
        resp = self._post_active(
            c,
            {
                "salutation_name": "特別な宛名",
                "salutation_name_is_manual": "",  # 自動に戻す
            },
        )
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertFalse(c.salutation_name_is_manual)
        self.assertEqual(c.salutation_name, "山田 様")
