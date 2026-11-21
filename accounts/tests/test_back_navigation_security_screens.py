"""Step 4-4b（二段階認証まわりの「戻る」導線と履歴持ち回り）のテスト。"""

import re
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.login_security.user_lookup import get_security_setting
from accounts.models import CustomUser, SecuritySetting
from back_navigator.back_navigator import BackNavigator


def _grant_perms(user: CustomUser, *codenames: str) -> None:
    perms = list(
        Permission.objects.filter(
            content_type__app_label="accounts",
            codename__in=codenames,
        )
    )
    if len(perms) != len(codenames):
        found = {p.codename for p in perms}
        missing = sorted(set(codenames) - found)
        raise AssertionError(f"Missing permissions: {missing}")
    user.user_permissions.add(*perms)


def _extract_link_href(html: str, target_path: str) -> str:
    pattern = re.compile(rf'href="({re.escape(target_path)}[^"]*)"')
    match = pattern.search(html)
    if not match:
        raise AssertionError(f"Link to {target_path} not found in HTML.")
    return match.group(1)


def _extract_back_href(html: str, label: str = "戻る") -> str:
    pattern = re.compile(
        rf'<a\s+class="app-btn app-btn--secondary"\s+href="([^"]+)">{re.escape(label)}</a>'
    )
    match = pattern.search(html)
    if not match:
        raise AssertionError(f"Back link ({label}) not found in HTML.")
    return match.group(1)


def _extract_hidden_back_value(html: str) -> str:
    pattern = re.compile(
        r'<input\s+type="hidden"\s+name="back_stack"\s+value="([^"]+)">'
    )
    match = pattern.search(html)
    if not match:
        raise AssertionError("Hidden back_stack field not found in HTML.")
    return match.group(1)


@override_settings(DEBUG=True)
class BackNavigationSecurityScreensTests(TestCase):
    """DEBUG=True 環境で各画面の戻るボタン・back_stack 持ち回り・POST 再表示を検証する。"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="bn_user",
            password="pw",
            email="bn_user@example.com",
        )
        self.admin = CustomUser.objects.create_user(
            username="bn_admin",
            password="pw",
            email="bn_admin@example.com",
        )
        _grant_perms(
            self.admin,
            "retire_user",
            "reset_two_factor",
            "manage_security_setting",
            "view_loginattempt",
        )
        self.target = CustomUser.objects.create_user(
            username="bn_target",
            password="pw",
            email="bn_target@example.com",
        )

    def _enroll(self, user: CustomUser) -> TOTPDevice:
        return TOTPDevice.objects.create(user=user, name="default", confirmed=True)

    def test_profile_and_settings_and_user_detail_links_carry_back_stack(self):
        self.client.force_login(self.user)

        # プロフィール → 二段階認証設定
        resp_profile = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp_profile.status_code, 200)
        settings_href = _extract_link_href(
            resp_profile.content.decode("utf-8"), reverse("accounts:two_factor")
        )
        self.assertIn("back_stack=", settings_href)

        # 未登録の設定画面 → 登録を開始
        resp_settings_unreg = self.client.get(settings_href)
        self.assertEqual(resp_settings_unreg.status_code, 200)
        setup_href = _extract_link_href(
            resp_settings_unreg.content.decode("utf-8"),
            reverse("accounts:two_factor_setup"),
        )
        self.assertIn("back_stack=", setup_href)

        # 登録済みの設定画面 → 再登録・再発行・無効化
        self._enroll(self.user)
        resp_settings_enr = self.client.get(settings_href)
        self.assertEqual(resp_settings_enr.status_code, 200)
        enr_html = resp_settings_enr.content.decode("utf-8")
        for url_name in (
            "accounts:two_factor_setup",
            "accounts:two_factor_recovery_codes",
            "accounts:two_factor_disable",
        ):
            with self.subTest(url_name=url_name):
                href = _extract_link_href(enr_html, reverse(url_name))
                self.assertIn("back_stack=", href)

        # ユーザー詳細 → 二段階認証リセット
        self.client.force_login(self.admin)
        detail_url = reverse(
            "accounts:user_detail", kwargs={"user_id": self.target.pk}
        )
        resp_detail = self.client.get(detail_url)
        self.assertEqual(resp_detail.status_code, 200)
        reset_href = _extract_link_href(
            resp_detail.content.decode("utf-8"),
            reverse("accounts:two_factor_reset", kwargs={"user_id": self.target.pk}),
        )
        self.assertIn("back_stack=", reset_href)

    def test_back_links_appear_with_back_stack_and_hidden_without(self):
        self._enroll(self.user)
        self.client.force_login(self.user)

        # back_stack なしでは設定・登録・再発行・無効化すべてに戻るボタンが出ない
        for url_name in (
            "accounts:two_factor",
            "accounts:two_factor_setup",
            "accounts:two_factor_recovery_codes",
            "accounts:two_factor_disable",
        ):
            with self.subTest(without_stack=url_name):
                resp = self.client.get(reverse(url_name))
                self.assertEqual(resp.status_code, 200)
                self.assertNotContains(resp, ">戻る</a>")
                self.assertNotContains(resp, ">最初に戻る</a>")

        # プロフィール → 設定（戻る = プロフィール）
        profile_url = reverse("accounts:profile")
        settings_url = reverse("accounts:two_factor")
        resp_profile = self.client.get(profile_url)
        settings_href = _extract_link_href(
            resp_profile.content.decode("utf-8"), settings_url
        )
        resp_settings = self.client.get(settings_href)
        self.assertEqual(resp_settings.status_code, 200)
        settings_html = resp_settings.content.decode("utf-8")
        self.assertEqual(_extract_back_href(settings_html), profile_url)
        self.assertNotIn(">最初に戻る</a>", settings_html)

        # 設定 → 登録・再発行・無効化（戻る = 設定、最初に戻る = プロフィール）
        for url_name in (
            "accounts:two_factor_setup",
            "accounts:two_factor_recovery_codes",
            "accounts:two_factor_disable",
        ):
            with self.subTest(with_stack=url_name):
                child_href = _extract_link_href(settings_html, reverse(url_name))
                resp_child = self.client.get(child_href)
                self.assertEqual(resp_child.status_code, 200)
                child_html = resp_child.content.decode("utf-8")
                back_href = _extract_back_href(child_html, "戻る")
                self.assertEqual(urlparse(back_href).path, settings_url)
                self.assertEqual(_extract_back_href(child_html, "最初に戻る"), profile_url)

        # ユーザー詳細 → リセット確認（back_stack 有無）
        self.client.force_login(self.admin)
        detail_url = reverse(
            "accounts:user_detail", kwargs={"user_id": self.target.pk}
        )
        reset_url = reverse(
            "accounts:two_factor_reset", kwargs={"user_id": self.target.pk}
        )
        resp_reset_plain = self.client.get(reset_url)
        self.assertEqual(resp_reset_plain.status_code, 200)
        self.assertNotContains(resp_reset_plain, ">戻る</a>")
        self.assertNotContains(resp_reset_plain, ">キャンセル</a>")

        resp_detail = self.client.get(detail_url)
        reset_href = _extract_link_href(resp_detail.content.decode("utf-8"), reset_url)
        resp_reset = self.client.get(reset_href)
        self.assertEqual(resp_reset.status_code, 200)
        reset_html = resp_reset.content.decode("utf-8")
        self.assertEqual(_extract_back_href(reset_html, "戻る"), detail_url)
        self.assertEqual(_extract_back_href(reset_html, "キャンセル"), detail_url)

    def test_post_forms_keep_hidden_back_field_and_preserve_back_on_error(self):
        # 1) 未登録 setup の start フォーム
        self.client.force_login(self.user)
        resp_prof = self.client.get(reverse("accounts:profile"))
        settings_href = _extract_link_href(
            resp_prof.content.decode("utf-8"), reverse("accounts:two_factor")
        )
        resp_set = self.client.get(settings_href)
        setup_href = _extract_link_href(
            resp_set.content.decode("utf-8"), reverse("accounts:two_factor_setup")
        )
        resp_setup_get = self.client.get(setup_href)
        hidden_start = _extract_hidden_back_value(resp_setup_get.content.decode("utf-8"))

        # start POST 後も confirm フォームに hidden_back_field と戻るが残る
        resp_setup_start = self.client.post(
            reverse("accounts:two_factor_setup"),
            {"action": "start", "back_stack": hidden_start},
        )
        self.assertEqual(resp_setup_start.status_code, 200)
        start_html = resp_setup_start.content.decode("utf-8")
        self.assertIn(reverse("accounts:two_factor"), _extract_back_href(start_html))
        hidden_confirm = _extract_hidden_back_value(start_html)
        pending = TOTPDevice.objects.get(user=self.user, confirmed=False)

        # confirm で誤ったコードを送っても戻るが残る
        resp_confirm_err = self.client.post(
            reverse("accounts:two_factor_setup"),
            {
                "action": "confirm",
                "device_id": str(pending.pk),
                "code": "000000",
                "back_stack": hidden_confirm,
            },
        )
        self.assertEqual(resp_confirm_err.status_code, 200)
        self.assertIn(
            reverse("accounts:two_factor"),
            _extract_back_href(resp_confirm_err.content.decode("utf-8")),
        )

        # 2) 登録済みの再登録開始・再発行・無効化の POST エラー再表示
        pending.confirmed = True
        pending.save(update_fields=["confirmed"])
        resp_set_enr = self.client.get(settings_href)
        set_enr_html = resp_set_enr.content.decode("utf-8")

        for url_name, post_data in (
            (
                "accounts:two_factor_setup",
                {"action": "reenroll_start", "code": "000000"},
            ),
            (
                "accounts:two_factor_recovery_codes",
                {"action": "regenerate", "code": "000000"},
            ),
            ("accounts:two_factor_disable", {"code": "000000"}),
        ):
            with self.subTest(post_error=url_name):
                url = reverse(url_name)
                href = _extract_link_href(set_enr_html, url)
                resp_get = self.client.get(href)
                hidden_val = _extract_hidden_back_value(resp_get.content.decode("utf-8"))
                resp_err = self.client.post(url, {**post_data, "back_stack": hidden_val})
                self.assertEqual(resp_err.status_code, 200)
                err_html = resp_err.content.decode("utf-8")
                self.assertIn(reverse("accounts:two_factor"), _extract_back_href(err_html))
                self.assertEqual(_extract_hidden_back_value(err_html), hidden_val)

        # 3) リセット確認の 501 文字エラー再表示
        self.client.force_login(self.admin)
        detail_url = reverse(
            "accounts:user_detail", kwargs={"user_id": self.target.pk}
        )
        reset_url = reverse(
            "accounts:two_factor_reset", kwargs={"user_id": self.target.pk}
        )
        resp_detail = self.client.get(detail_url)
        reset_href = _extract_link_href(resp_detail.content.decode("utf-8"), reset_url)
        resp_reset_get = self.client.get(reset_href)
        hidden_reset = _extract_hidden_back_value(resp_reset_get.content.decode("utf-8"))
        resp_reset_err = self.client.post(
            reset_url, {"reason_note": "a" * 501, "back_stack": hidden_reset}
        )
        self.assertEqual(resp_reset_err.status_code, 400)
        reset_err_html = resp_reset_err.content.decode("utf-8")
        self.assertEqual(_extract_back_href(reset_err_html, "戻る"), detail_url)
        self.assertEqual(_extract_back_href(reset_err_html, "キャンセル"), detail_url)
        self.assertEqual(_extract_hidden_back_value(reset_err_html), hidden_reset)

    def test_forced_enrollment_redirect_has_no_back_button_and_returns_200(self):
        setting = get_security_setting()
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()

        self.client.force_login(self.user)
        resp = self.client.get(reverse("accounts:profile"), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("accounts:two_factor_setup"))
        self.assertNotContains(resp, ">戻る</a>")
        self.assertNotContains(resp, ">最初に戻る</a>")

    def test_sidebar_root_screens_have_no_back_buttons(self):
        self.client.force_login(self.admin)
        fake_stack = BackNavigator(self.client.get(reverse("accounts:profile")).wsgi_request)
        _ = fake_stack
        sample_query = parse_qs(
            urlparse(
                _extract_link_href(
                    self.client.get(reverse("accounts:profile")).content.decode("utf-8"),
                    reverse("accounts:two_factor"),
                )
            ).query
        )["back_stack"][0]

        for url_name in ("accounts:security_setting", "accounts:login_attempt_list"):
            for with_stack in (False, True):
                with self.subTest(url_name=url_name, with_stack=with_stack):
                    url = reverse(url_name)
                    if with_stack:
                        resp = self.client.get(url, {"back_stack": sample_query})
                    else:
                        resp = self.client.get(url)
                    self.assertEqual(resp.status_code, 200)
                    self.assertNotContains(resp, ">戻る</a>")
                    self.assertNotContains(resp, ">最初に戻る</a>")
