"""dev_create_dup_test_data 管理コマンド（開発ツール、v1.6.1 重複検出テストデータ）。

Phase F2（マージ画面 ContactSns 比較表示）の視覚確認用に、重複検出
（check_duplicates cron）で確実に DuplicateCandidate が生成されるテストデータを作成する。

設計概要：
- 4 バラエティ：clone（完全同一ペア）/ minor_diff（typo レベル）/
  major_diff（改姓・転職・部署変更ミックス）/ noise（単独）
- ペアは別 Person × 2（重複検出の対象）
- ContactSns は各 Contact に 0〜3 件、バラエティに応じて diff のあり方を変える
- ContactFieldConfidence レコードは一切作成しない（指示書 §3.6、CFC 非作成で疑似 high 扱い）
- v1.6.1 全フィールドに値を入れる（normalization の derive_org_core_name /
  compute_salutation_name / compose_full_address を直接呼ぶ）
- DuplicateCandidate / OriginalImage / BusinessCard / PersonMergeLog は生成しない

借用：旧スクリプト `dev_create_test_contact_data` から
LAST_NAMES / MALE_FIRST_NAMES / FEMALE_FIRST_NAMES / COMPANIES の 4 定数のみ import。

専用ユーザー：
- 既定 `test_data_user`（--user で変更可）を Django Admin で事前作成しておく前提
- 起動時に取得のみ。存在しなければエラー終了（自動作成しない）
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cards.management.commands.dev_create_test_contact_data import (
    COMPANIES,
    FEMALE_FIRST_NAMES,
    LAST_NAMES,
    MALE_FIRST_NAMES,
)
from contacts.models import Contact, ContactSns
from contacts.services.normalization import (
    compose_full_address,
    compute_salutation_name,
    derive_org_core_name,
)
from persons.models import Person


User = get_user_model()

DEFAULT_USER = "test_data_user"
DEFAULT_TOTAL = 100

# 4 バラエティの按分比率（合計 1.0）。--total に対するこの比率で件数を決める。
# clone / minor_diff / major_diff はペア生成（2 件単位）、noise は単独（1 件単位）。
VARIETY_RATIO = {
    "clone": 0.20,
    "minor_diff": 0.30,
    "major_diff": 0.30,
    "noise": 0.20,
}


# ----------------------------------------------------------------------
# 独自プール定義（旧スクリプトの DEPARTMENTS / TITLES は流用せず新規に定義）
# ----------------------------------------------------------------------

DEPARTMENTS: List[str] = [
    "営業部", "営業1部", "営業2部", "営業企画部",
    "技術部", "開発部", "システム部", "情報システム部",
    "総務部", "人事部", "経理部", "総務人事部",
    "製造部", "品質管理部", "生産管理部",
    "マーケティング部", "広報部", "企画部", "経営企画部",
    "カスタマーサポート部",
]

TITLES: List[str] = [
    "", "主任", "係長", "課長", "次長", "部長", "本部長",
    "取締役", "常務取締役", "専務取締役", "代表取締役",
]

QUALIFICATIONS: List[str] = [
    "", "中小企業診断士", "情報処理安全確保支援士", "簿記2級",
    "TOEIC 800点", "MBA", "宅地建物取引士",
]

CATCHPHRASES: List[str] = [
    "", "お気軽にご相談ください", "迅速・丁寧をモットーに",
    "技術で未来を創る", "あなたの課題、私たちが解決します",
]


# ----------------------------------------------------------------------
# 純関数群（DB 操作なし）
# ----------------------------------------------------------------------


def _detect_legal_entity(org_name: str) -> Tuple[str, str, str]:
    """[性質] 純関数。会社名から法人格・位置・コードを判定する。

    [入力] org_name: 会社名フル
    [出力] (legal_entity_type, position, code)。法人格なしなら 3 つとも空文字。
    """
    for legal_type in ("株式会社", "有限会社", "合同会社"):
        if org_name.startswith(legal_type):
            return legal_type, "Pre", "CP"
        if org_name.endswith(legal_type):
            return legal_type, "Post", "CP"
    return "", "", ""


_ADDR_PREFECTURE_RE = re.compile(r"^(.+?[都道府県])(.*)$")
_ADDR_CITY_KU_RE = re.compile(r"^(.+?市)(.+?区)(.*)$")
_ADDR_MUNI_RE = re.compile(r"^(.+?(?:市|区|町|村|郡))(.*)$")


def _split_jp_address(addr: str) -> Tuple[str, str, str]:
    """[性質] 純関数。日本語住所文字列を region / city / rest に分割する。

    [入力] addr: 「東京都千代田区丸の内1-2-3...」のような結合済み住所
    [出力] (region, city, rest_of_address)。判別不能なら空文字を含む。
    """
    m = _ADDR_PREFECTURE_RE.match(addr)
    if not m:
        return "", "", addr
    region, remain = m.group(1), m.group(2)
    m_ku = _ADDR_CITY_KU_RE.match(remain)
    if m_ku:
        return region, m_ku.group(1) + m_ku.group(2), m_ku.group(3)
    m_muni = _ADDR_MUNI_RE.match(remain)
    if m_muni:
        return region, m_muni.group(1), m_muni.group(2)
    return region, "", remain


def _gen_mobile(rng: random.Random) -> str:
    """[性質] 純関数（rng 依存）。携帯番号を生成（数字のみ、正規化済み）。"""
    prefix = rng.choice(["070", "080", "090"])
    return f"{prefix}{rng.randint(10000000, 99999999)}"


def _gen_email_local(rng: random.Random, last_rom: str, first_rom: str) -> str:
    """[性質] 純関数（rng 依存）。個人メールローカル部を生成。"""
    pattern = rng.randint(0, 3)
    if pattern == 0:
        return f"{first_rom}.{last_rom}"
    if pattern == 1:
        return f"{last_rom}.{first_rom}{rng.randint(1, 99)}"
    if pattern == 2:
        return f"{first_rom}_{last_rom}"
    return f"{first_rom[0]}{last_rom}{rng.randint(10, 999)}"


def _gen_sns_id(rng: random.Random, sns_type: str, seed: str) -> str:
    """[性質] 純関数（rng 依存）。SNS sns_id を sns_type 別パターンで生成する。"""
    if sns_type == "twitter":
        return f"@{seed}{rng.randint(1, 999)}"
    if sns_type == "linkedin":
        return f"https://linkedin.com/in/{seed}-{rng.randint(100, 9999)}"
    if sns_type == "facebook":
        return f"https://facebook.com/{seed}.{rng.randint(1, 999)}"
    if sns_type == "instagram":
        return f"@{seed}_{rng.randint(1, 99)}"
    if sns_type == "github":
        return f"{seed}-dev{rng.randint(1, 99)}"
    if sns_type == "blog":
        return f"https://{seed}.example.com/blog"
    if sns_type == "youtube":
        return f"https://youtube.com/@{seed}-channel"
    if sns_type == "line":
        return f"{seed}.line{rng.randint(1, 99)}"
    return f"id_{seed}"


def _gen_sns_entries(
    rng: random.Random, seed: str, count: int
) -> List[Tuple[str, str]]:
    """[性質] 純関数（rng 依存）。SnsType の choices からランダムに count 件選び生成。

    UniqueConstraint(contact, sns_type, sns_id) 違反を避けるため sns_type の重複は許さない
    （= 1 Contact に同 sns_type は最大 1 件）。choices 8 種なので count<=8 で破綻しない。
    """
    available = list(ContactSns.SnsType.values)
    rng.shuffle(available)
    return [(t, _gen_sns_id(rng, t, seed)) for t in available[:count]]


def _pick_name(rng: random.Random) -> Dict[str, str]:
    """[性質] 純関数（rng 依存）。日本人氏名 1 件を組み立てる（性別ランダム）。"""
    last_ja, last_rom = rng.choice(LAST_NAMES)
    if rng.random() < 0.5:
        first_ja, first_rom = rng.choice(MALE_FIRST_NAMES)
    else:
        first_ja, first_rom = rng.choice(FEMALE_FIRST_NAMES)
    return {
        "last_ja": last_ja,
        "first_ja": first_ja,
        "last_rom": last_rom,
        "first_rom": first_rom,
        "full_name": f"{last_ja}{first_ja}",
        "phonetic": f"{last_rom} {first_rom}",
    }


def _pick_company_location(
    rng: random.Random, prefer_branch: bool = False
) -> Dict[str, str]:
    """[性質] 純関数（rng 依存）。会社・拠点（任意）を 1 件選び、所属系フィールドを返す。"""
    company = rng.choice(COMPANIES)
    use_branch = bool(company.branches) and (
        prefer_branch or rng.random() < 0.4
    )
    if use_branch:
        branch = rng.choice(company.branches)
        branch_name = branch.name
        postal = branch.postal_code
        addr_raw = branch.address
        loc_phone = branch.phone
        loc_fax = branch.fax
    else:
        branch_name = ""
        postal = company.hq_postal_code
        addr_raw = company.hq_address
        loc_phone = company.hq_phone
        loc_fax = company.hq_fax
    region, city, rest = _split_jp_address(addr_raw)
    legal_type, legal_pos, legal_code = _detect_legal_entity(company.name)
    org_core = derive_org_core_name(company.name, legal_type)
    return {
        "organization": company.name,
        "domain": company.domain,
        "legal_entity_type": legal_type,
        "legal_entity_type_position": legal_pos,
        "legal_entity_type_code": legal_code,
        "org_core_name": org_core,
        "branch": branch_name,
        "postal_code": postal,
        "region": region,
        "city": city,
        "rest_of_address": rest,
        "loc_phone": loc_phone,
        "loc_fax": loc_fax,
        "org_phone": company.hq_phone,
        "org_fax": company.hq_fax,
    }


def _build_draft(
    rng: random.Random, group_label: str
) -> Tuple[Dict, List[Tuple[str, str]]]:
    """[性質] 純関数（rng 依存・DB 操作なし）。Contact 1 件分の全フィールド辞書を生成。

    戻り値の draft は Contact.objects.create() にそのまま渡せる kwargs 形式。
    salutation_name / address は本ヘルパで完成させ、Contact.save() の自動再計算が
    発火しないよう source 群との整合を取る（snapshot ≡ field 値）。
    """
    name = _pick_name(rng)
    loc = _pick_company_location(rng)
    email_local = _gen_email_local(rng, name["last_rom"], name["first_rom"])
    email = f"{email_local}@{loc['domain']}"
    mobile = _gen_mobile(rng)

    draft = {
        # 名前系
        "full_name": name["full_name"],
        "last_name": name["last_ja"],
        "first_name": name["first_ja"],
        "other_name_parts": "",
        "name_order": Contact.NameOrder.LAST_FIRST,
        "display_name": name["full_name"],
        "phonetic_name": name["phonetic"],
        "alias_name": "",
        # 会社系
        "organization": loc["organization"],
        "legal_entity_type": loc["legal_entity_type"],
        "legal_entity_type_position": loc["legal_entity_type_position"],
        "legal_entity_type_code": loc["legal_entity_type_code"],
        "org_core_name": loc["org_core_name"],
        "org_domain_name": loc["domain"],
        "department": rng.choice(DEPARTMENTS),
        "title": rng.choice(TITLES),
        "qualification": rng.choice(QUALIFICATIONS),
        "catchphrase": rng.choice(CATCHPHRASES),
        "branch": loc["branch"],
        # 住所系
        "postal_code": loc["postal_code"],
        "country": "JP",
        "region": loc["region"],
        "city": loc["city"],
        "rest_of_address": loc["rest_of_address"],
        # 連絡先系
        "email": email,
        "personal_phone": loc["loc_phone"],
        "mobile_phone": mobile,
        "personal_fax": loc["loc_fax"],
        "org_phone": loc["org_phone"],
        "org_fax": loc["org_fax"],
        "website": f"https://{loc['domain']}/",
        # メモ・言語
        "notes": "",
        "lang": "ja",
        "language_composition": Contact.LanguageComposition.LOCAL_ONLY,
        "handwritten_text": "",
        "other_printed_text": "",
    }
    # address は compose_full_address で組み立てた完成形を入れる（save() で再 compose
    # されないよう source ≡ snapshot を担保）
    draft["address"] = compose_full_address(
        draft["postal_code"],
        draft["region"],
        draft["city"],
        draft["rest_of_address"],
        draft["country"],
        draft["lang"],
    )
    # salutation_name は compute_salutation_name() を直接呼んで埋める（指示書 §3.4）
    draft["salutation_name"] = _compute_salutation_for_draft(draft)

    sns_entries = _gen_sns_entries(
        rng, f"{name['last_rom']}.{name['first_rom']}", rng.randint(0, 3)
    )
    return draft, sns_entries


class _SalutationProbe:
    """compute_salutation_name() に渡す duck typing 用の軽量オブジェクト。

    Contact インスタンスを使うとシグナル等の副作用が走るため、属性のみ提供する。
    """

    def __init__(self, lang: str, last_name: str, full_name: str):
        self.lang = lang
        self.last_name = last_name
        self.full_name = full_name


def _compute_salutation_for_draft(draft: Dict) -> str:
    return compute_salutation_name(
        _SalutationProbe(draft["lang"], draft["last_name"], draft["full_name"])
    )


def _rebuild_address(draft: Dict) -> None:
    """住所構成要素 4 件を変更した後に address を再 compose して draft に反映する。"""
    draft["address"] = compose_full_address(
        draft["postal_code"],
        draft["region"],
        draft["city"],
        draft["rest_of_address"],
        draft["country"],
        draft["lang"],
    )


def _apply_name_change(draft: Dict, rng: random.Random) -> None:
    """[性質] 副作用あり（draft を mutate）。改姓を適用し依存フィールドも更新する。"""
    new_last_ja, _ = rng.choice(LAST_NAMES)
    while new_last_ja == draft["last_name"]:
        new_last_ja, _ = rng.choice(LAST_NAMES)
    draft["last_name"] = new_last_ja
    draft["full_name"] = f"{new_last_ja}{draft['first_name']}"
    draft["display_name"] = draft["full_name"]
    draft["salutation_name"] = _compute_salutation_for_draft(draft)


def _apply_company_change(draft: Dict, rng: random.Random) -> None:
    """[性質] 副作用あり（draft を mutate）。転職（会社・住所・連絡先一式変更）を適用する。"""
    new_loc = _pick_company_location(rng)
    # 元と同一会社なら別を引き直す（5 回まで）
    for _ in range(5):
        if new_loc["organization"] != draft["organization"]:
            break
        new_loc = _pick_company_location(rng)
    draft["organization"] = new_loc["organization"]
    draft["legal_entity_type"] = new_loc["legal_entity_type"]
    draft["legal_entity_type_position"] = new_loc["legal_entity_type_position"]
    draft["legal_entity_type_code"] = new_loc["legal_entity_type_code"]
    draft["org_core_name"] = new_loc["org_core_name"]
    draft["org_domain_name"] = new_loc["domain"]
    draft["branch"] = new_loc["branch"]
    draft["department"] = rng.choice(DEPARTMENTS)
    draft["title"] = rng.choice(TITLES)
    draft["postal_code"] = new_loc["postal_code"]
    draft["region"] = new_loc["region"]
    draft["city"] = new_loc["city"]
    draft["rest_of_address"] = new_loc["rest_of_address"]
    draft["personal_phone"] = new_loc["loc_phone"]
    draft["personal_fax"] = new_loc["loc_fax"]
    draft["org_phone"] = new_loc["org_phone"]
    draft["org_fax"] = new_loc["org_fax"]
    draft["website"] = f"https://{new_loc['domain']}/"
    _rebuild_address(draft)


# ----------------------------------------------------------------------
# バラエティ別ジェネレータ（純関数：dict のリストを返すだけ・DB 操作なし）
# ----------------------------------------------------------------------


@dataclass
class GeneratedContact:
    """1 件分の生成結果。DB 書込み単位。"""

    draft: Dict
    sns_entries: List[Tuple[str, str]]
    label: str


def _generate_clone_pair(rng: random.Random) -> List[GeneratedContact]:
    """完全同一ペア（exact_match を目指す）。全フィールド一致＋同じ SNS 構成。"""
    draft, sns = _build_draft(rng, "clone")
    return [
        GeneratedContact(dict(draft), list(sns), "clone-A"),
        GeneratedContact(dict(draft), list(sns), "clone-B"),
    ]


def _generate_minor_diff_pair(rng: random.Random) -> List[GeneratedContact]:
    """typo レベルの違いを 1〜2 フィールドに含むペア。

    mobile_phone と email は維持する（possible_high / exact_match レンジを担保）。
    差分は department / title / qualification / catchphrase のいずれか。SNS は片側に
    1 件追加または削除する（部分 diff）。
    """
    a_draft, a_sns = _build_draft(rng, "minor_diff")
    b_draft = dict(a_draft)
    diff_field = rng.choice(["department", "title", "qualification", "catchphrase"])
    pool = {
        "department": DEPARTMENTS,
        "title": TITLES,
        "qualification": QUALIFICATIONS,
        "catchphrase": CATCHPHRASES,
    }[diff_field]
    new_val = rng.choice(pool)
    while new_val == a_draft[diff_field]:
        new_val = rng.choice(pool)
    b_draft[diff_field] = new_val
    # SNS：片方に 1 件足す（uniq 担保のため使用中タイプを除く）
    used = {t for t, _ in a_sns}
    avail = [t for t in ContactSns.SnsType.values if t not in used]
    b_sns = list(a_sns)
    if avail:
        extra_type = rng.choice(avail)
        b_sns.append(
            (
                extra_type,
                _gen_sns_id(
                    rng,
                    extra_type,
                    f"extra{rng.randint(1, 99)}",
                ),
            )
        )
    return [
        GeneratedContact(a_draft, list(a_sns), "minor_diff-A"),
        GeneratedContact(b_draft, b_sns, "minor_diff-B"),
    ]


def _generate_major_diff_pair(rng: random.Random) -> List[GeneratedContact]:
    """改姓・転職・部署変更が同時に乗るペア。

    重複検出絞り込み（§8.10.2）の OR 条件（フルネーム一致 / メール一致 / 携帯一致）を
    担保するため、mobile_phone のみ維持する。SNS は大きく入れ替える。
    """
    a_draft, a_sns = _build_draft(rng, "major_diff")
    b_draft = dict(a_draft)
    _apply_name_change(b_draft, rng)
    _apply_company_change(b_draft, rng)
    # メールも会社ドメイン変化に追随（mobile だけ維持）
    b_local = _gen_email_local(rng, b_draft["last_name"], b_draft["first_name"][:3])
    b_draft["email"] = f"{b_local}@{b_draft['org_domain_name']}"
    # SNS は全部入れ替え
    b_sns = _gen_sns_entries(
        rng,
        f"new{rng.randint(1, 999)}",
        rng.randint(0, 3),
    )
    return [
        GeneratedContact(a_draft, list(a_sns), "major_diff-A"),
        GeneratedContact(b_draft, b_sns, "major_diff-B"),
    ]


def _generate_noise(rng: random.Random) -> List[GeneratedContact]:
    """単独 Contact（重複ペアを作らない）。SNS は 0〜2 件。"""
    draft, _ = _build_draft(rng, "noise")
    sns = _gen_sns_entries(
        rng,
        f"noise{rng.randint(1, 999)}",
        rng.randint(0, 2),
    )
    return [GeneratedContact(draft, sns, "noise")]


# ----------------------------------------------------------------------
# DB 書込み（副作用あり）
# ----------------------------------------------------------------------


def _save_generated_contacts(items: List[GeneratedContact], user) -> None:
    """[性質] 副作用あり（DB 書込）。生成された Contact 群を DB に保存する。

    1 件ずつ Person → Contact → Person.primary_contact 同期 → ContactSns の順で作成。
    ContactFieldConfidence は一切作成しない（指示書 §3.6、CFC 非作成で疑似 high 扱い）。
    トランザクションは件数が多くなり得るため呼び出し側で切る（atomic を都度切らない）。
    """
    for item in items:
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            business_card=None,
            person=person,
            status=Contact.Status.PRIMARY,
            created_by=user,
            updated_by=user,
            **item.draft,
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        for sns_type, sns_id in item.sns_entries:
            ContactSns.objects.create(
                contact=contact, sns_type=sns_type, sns_id=sns_id
            )


def _reset_test_data(user, stdout) -> Dict[str, int]:
    """[性質] 副作用あり（DB 削除）。指定 user 由来の Contact / Person / ContactSns / CFC を削除。

    Contact を delete() するとカスケードで ContactSns と CFC は自動削除される。
    孤児 Person のみ別途検出して物理削除する。
    """
    affected_person_ids = set(
        Contact.objects.filter(created_by=user).values_list("person_id", flat=True)
    )
    _total, per_model = Contact.objects.filter(created_by=user).delete()
    deleted_contacts = per_model.get(Contact._meta.label, 0)
    deleted_sns = per_model.get(ContactSns._meta.label, 0)
    deleted_persons = 0
    for pid in affected_person_ids:
        if not Contact.objects.filter(person_id=pid).exists():
            Person.objects.filter(id=pid).delete()
            deleted_persons += 1
    stdout.write(
        f"reset: deleted contacts={deleted_contacts}, "
        f"sns cascaded={deleted_sns}, orphan persons={deleted_persons}"
    )
    return {
        "contacts": deleted_contacts,
        "sns": deleted_sns,
        "persons": deleted_persons,
    }


# ----------------------------------------------------------------------
# Command 本体
# ----------------------------------------------------------------------


class Command(BaseCommand):
    help = "v1.6.1 重複検出テスト用の Contact データを生成する（Phase F2 視覚確認用）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="既存テストデータ（指定 user 由来）を削除してから生成する",
        )
        parser.add_argument(
            "--user",
            default=DEFAULT_USER,
            help=f"生成者ユーザー名（既定 {DEFAULT_USER}）",
        )
        parser.add_argument(
            "--total",
            type=int,
            default=DEFAULT_TOTAL,
            help=f"総 Contact 生成件数（既定 {DEFAULT_TOTAL}）",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="乱数シード（既定 None：毎回ランダム）",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="1 件ごとの詳細をログ出力",
        )

    def handle(self, *args, **opts):
        username = opts["user"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(
                f"ユーザー '{username}' が存在しません。"
                f"Django Admin で事前作成してから実行してください。"
                f"本コマンドはユーザーを作成しません。"
            )

        total = opts["total"]
        if total < 0:
            raise CommandError("--total は 0 以上で指定してください。")

        rng = random.Random(opts["seed"])

        with transaction.atomic():
            if opts["reset"]:
                _reset_test_data(user, self.stdout)

            generated = self._build_all(rng, total)
            _save_generated_contacts(generated, user)

        breakdown = self._tally(generated)
        self.stdout.write(
            self.style.SUCCESS(
                f"生成完了：合計 {len(generated)} 件 "
                f"(clone={breakdown['clone']} / "
                f"minor_diff={breakdown['minor_diff']} / "
                f"major_diff={breakdown['major_diff']} / "
                f"noise={breakdown['noise']})"
            )
        )
        if opts["verbose"]:
            for idx, item in enumerate(generated, 1):
                d = item.draft
                self.stdout.write(
                    f"  [{idx:03d}][{item.label:>14s}] "
                    f"{d['full_name']:<10s} | {d['organization']} / "
                    f"{d['department']} / {d['title'] or '(平)'} | "
                    f"mobile={d['mobile_phone']} email={d['email']} "
                    f"SNS={len(item.sns_entries)}"
                )

    def _build_all(
        self, rng: random.Random, total: int
    ) -> List[GeneratedContact]:
        """[性質] 純関数（rng 依存）。VARIETY_RATIO に従い 4 バラエティ分を生成する。

        clone / minor_diff / major_diff はペア（2 件単位）に丸め、残りを noise に回す。
        """
        n_clone = int(total * VARIETY_RATIO["clone"])
        n_minor = int(total * VARIETY_RATIO["minor_diff"])
        n_major = int(total * VARIETY_RATIO["major_diff"])
        # ペア生成は 2 件単位なので奇数件は切り下げ
        n_clone -= n_clone % 2
        n_minor -= n_minor % 2
        n_major -= n_major % 2
        n_noise = total - n_clone - n_minor - n_major

        items: List[GeneratedContact] = []
        for _ in range(n_clone // 2):
            items.extend(_generate_clone_pair(rng))
        for _ in range(n_minor // 2):
            items.extend(_generate_minor_diff_pair(rng))
        for _ in range(n_major // 2):
            items.extend(_generate_major_diff_pair(rng))
        for _ in range(n_noise):
            items.extend(_generate_noise(rng))
        return items

    def _tally(self, items: List[GeneratedContact]) -> Dict[str, int]:
        result = {"clone": 0, "minor_diff": 0, "major_diff": 0, "noise": 0}
        for item in items:
            base = item.label.split("-", 1)[0]
            if base in result:
                result[base] += 1
        return result
