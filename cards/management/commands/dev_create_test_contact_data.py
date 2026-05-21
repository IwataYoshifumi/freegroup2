"""dev_create_test_contact_data 管理コマンド（開発ツール）。

v1.4.2 系統X / 系統Y / C ブロックの動作確認用テストデータを生成する
（v1.6.0 で Contact フィールドのリネーム / personal_phone・personal_fax の JSONField 化に追従）。
Contact + Person + ContactFieldConfidence のみ生成し、BusinessCard / OriginalImage は
作らない（OCR 経路は再現しない方針）。

設計概要：
- 生成は 5 グループ：① 完全同一名刺 / ② 異動・転職 / ③ 改姓・部署変更 /
  ④ 同姓同名 / ⑤ ノイズ
- 各 Contact に 1:1 で新規 Person を作る（cron 実行前のシード状態を再現）
- ContactFieldConfidence は §4.6.1 / §10.6.4 に従い low / mid のみ DB レコード化、
  high はレコード作らない（CheckConstraint で物理禁止）
- プール値は §15.5.3 の正規化ルールで予め整形済み（電話=数字のみ、メール=小文字、
  住所=空白なし／丁目・番地・号→ハイフン、会社=前株/後株別扱い）
- --seed で再現可能。--dry-run で DB に書かず内容表示

専用ユーザー：
- `test_data_user` を Django Admin で事前作成しておく前提
- 起動時に取得のみ。存在しなければエラー終了（自動作成しない）

ファイル名・コマンド名のプレフィックスは `dev_*`（開発ツール、§13.5 別表B）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from config.constants import DUPLICATE_CHECK_FIELDS
from contacts.models import Contact, ContactFieldConfidence
from persons.models import Person


User = get_user_model()

TEST_USER_USERNAME = "test_data_user"
DEFAULT_SEED = 42

FREE_EMAIL_DOMAINS = ["gmail.com", "yahoo.co.jp", "outlook.com"]

# random モードの confidence 分布（仕様書 §10.6.4 / OCR の現実的な分布の再現）。
# 値は (high, mid, low) の確率（合計 1.0）。
# personal_fax は DUPLICATE_CHECK_FIELDS には含まれないが、タスク仕様で分布が指定されているため
# confidence レコード生成対象に含める（DB 制約上は field_name 自由）。
CONFIDENCE_DISTRIBUTION = {
    "full_name":      (0.80, 0.20, 0.00),
    "email":          (0.70, 0.25, 0.05),
    "mobile_phone":   (0.70, 0.25, 0.05),
    "personal_phone": (0.70, 0.25, 0.05),
    "personal_fax":   (0.70, 0.25, 0.05),
    "organization":   (0.75, 0.20, 0.05),
    "department":     (0.65, 0.25, 0.10),
    "title":          (0.65, 0.25, 0.10),
    "branch":         (0.65, 0.25, 0.10),
    "address":        (0.60, 0.30, 0.10),
}

# confidence を割り当てる対象フィールド一覧。
TRACKED_CONFIDENCE_FIELDS: List[str] = list(CONFIDENCE_DISTRIBUTION.keys())


# =====================================================================
# プール定義
# =====================================================================

# プール1：氏名（30 件 + 男性名 20 件 + 女性名 20 件）
LAST_NAMES: List[Tuple[str, str]] = [
    ("佐藤", "sato"),
    ("鈴木", "suzuki"),
    ("高橋", "takahashi"),
    ("田中", "tanaka"),
    ("伊藤", "ito"),
    ("渡辺", "watanabe"),
    ("山本", "yamamoto"),
    ("中村", "nakamura"),
    ("小林", "kobayashi"),
    ("加藤", "kato"),
    ("吉田", "yoshida"),
    ("山田", "yamada"),
    ("佐々木", "sasaki"),
    ("山口", "yamaguchi"),
    ("斎藤", "saito"),
    ("松本", "matsumoto"),
    ("井上", "inoue"),
    ("木村", "kimura"),
    ("林", "hayashi"),
    ("清水", "shimizu"),
    ("山崎", "yamazaki"),
    ("森", "mori"),
    ("阿部", "abe"),
    ("池田", "ikeda"),
    ("橋本", "hashimoto"),
    ("石川", "ishikawa"),
    ("中島", "nakajima"),
    ("前田", "maeda"),
    ("藤田", "fujita"),
    ("後藤", "goto"),
]

MALE_FIRST_NAMES: List[Tuple[str, str]] = [
    ("健", "ken"),
    ("太郎", "taro"),
    ("一郎", "ichiro"),
    ("次郎", "jiro"),
    ("三郎", "saburo"),
    ("浩", "hiroshi"),
    ("誠", "makoto"),
    ("学", "manabu"),
    ("剛", "tsuyoshi"),
    ("達也", "tatsuya"),
    ("直樹", "naoki"),
    ("和也", "kazuya"),
    ("哲也", "tetsuya"),
    ("拓也", "takuya"),
    ("翔太", "shota"),
    ("亮", "ryo"),
    ("優", "yutaka"),
    ("大輔", "daisuke"),
    ("健一", "kenichi"),
    ("正人", "masato"),
]

FEMALE_FIRST_NAMES: List[Tuple[str, str]] = [
    ("花子", "hanako"),
    ("由美", "yumi"),
    ("美咲", "misaki"),
    ("愛", "ai"),
    ("舞", "mai"),
    ("彩", "aya"),
    ("沙織", "saori"),
    ("真理子", "mariko"),
    ("純子", "junko"),
    ("洋子", "yoko"),
    ("陽子", "youko"),
    ("理恵", "rie"),
    ("智子", "tomoko"),
    ("綾子", "ayako"),
    ("恵子", "keiko"),
    ("加奈", "kana"),
    ("結衣", "yui"),
    ("彩花", "ayaka"),
    ("奈々", "nana"),
    ("優子", "yuko"),
]


@dataclass
class Branch:
    """会社の拠点情報（プール2 構造の一部）。"""

    name: str
    phone: str  # 数字のみ（§15.5.3）
    fax: str
    postal_code: str  # 数字のみ
    address: str  # 正規化済み


@dataclass
class Company:
    """会社情報（プール2 の 1 セット）。

    ・name は前株/後株/有限会社/合同会社/表記なしのバリエーション
    ・generic_email_local は §14.3.6 の DUPLICATE_GENERIC_EMAIL_LOCALPARTS から
    """

    name: str
    domain: str
    hq_phone: str
    hq_fax: str
    generic_email_local: str
    hq_postal_code: str
    hq_address: str
    branches: List[Branch] = field(default_factory=list)


# プール2：会社プール（10 社、各 2〜3 拠点）。前株/後株/有限/合同/表記なしを混在。
COMPANIES: List[Company] = [
    Company(
        name="株式会社明治テック",
        domain="meiji-tech.co.jp",
        hq_phone="0312345678",
        hq_fax="0312345679",
        generic_email_local="info",
        hq_postal_code="1000005",
        hq_address="東京都千代田区丸の内1-2-3明治ビル",
        branches=[
            Branch("大阪支店", "0612345678", "0612345679",
                   "5300001", "大阪府大阪市北区梅田4-5-6梅田センタービル"),
            Branch("名古屋支店", "0521234567", "0521234568",
                   "4500002", "愛知県名古屋市中村区名駅1-2-3"),
        ],
    ),
    Company(
        name="昭和商事株式会社",
        domain="showa-shoji.co.jp",
        hq_phone="0334567890",
        hq_fax="0334567891",
        generic_email_local="contact",
        hq_postal_code="1040061",
        hq_address="東京都中央区銀座2-3-4昭和タワー",
        branches=[
            Branch("横浜営業所", "0451234567", "0451234568",
                   "2200004", "神奈川県横浜市西区北幸1-2-3"),
            Branch("福岡営業所", "0921234567", "0921234568",
                   "8120011", "福岡県福岡市博多区博多駅前2-3-4"),
            Branch("札幌営業所", "0111234567", "0111234568",
                   "0600001", "北海道札幌市中央区北一条西4-5-6"),
        ],
    ),
    Company(
        name="平成電機株式会社",
        domain="heisei-denki.co.jp",
        hq_phone="0456789012",
        hq_fax="0456789013",
        generic_email_local="support",
        hq_postal_code="2310005",
        hq_address="神奈川県横浜市中区本町1-2-3平成ビル",
        branches=[
            Branch("東京支社", "0345678901", "0345678902",
                   "1050001", "東京都港区虎ノ門3-4-5"),
            Branch("仙台営業所", "0221234567", "0221234568",
                   "9800021", "宮城県仙台市青葉区中央1-2-3"),
        ],
    ),
    Company(
        name="令和ソフトウェア株式会社",
        domain="reiwa-soft.co.jp",
        hq_phone="0521112222",
        hq_fax="0521112223",
        generic_email_local="sales",
        hq_postal_code="4600003",
        hq_address="愛知県名古屋市中区錦2-3-4令和ビル",
        branches=[
            Branch("東京支社", "0354321098", "0354321099",
                   "1010021", "東京都千代田区外神田1-2-3"),
            Branch("京都支社", "0751234567", "0751234568",
                   "6048005", "京都府京都市中京区烏丸通御池上る"),
        ],
    ),
    Company(
        name="有限会社青空工業",
        domain="aozora-kogyo.co.jp",
        hq_phone="0565111222",
        hq_fax="0565111223",
        generic_email_local="office",
        hq_postal_code="4710025",
        hq_address="愛知県豊田市西町1-2-3",
        branches=[
            Branch("豊田南工場", "0565222333", "0565222334",
                   "4710861", "愛知県豊田市新生町5-6-7"),
        ],
    ),
    Company(
        name="合同会社みらい研究所",
        domain="mirai-lab.jp",
        hq_phone="0312223333",
        hq_fax="0312223334",
        generic_email_local="info",
        hq_postal_code="1130033",
        hq_address="東京都文京区本郷7-3-1東京大学アントレラボ",
        branches=[],
    ),
    Company(
        name="日本物流株式会社",
        domain="nihon-logi.co.jp",
        hq_phone="0667778888",
        hq_fax="0667778889",
        generic_email_local="customer",
        hq_postal_code="5500004",
        hq_address="大阪府大阪市西区靭本町1-2-3",
        branches=[
            Branch("東京物流センター", "0489901234", "0489901235",
                   "2790031", "千葉県浦安市舞浜1-2-3"),
            Branch("神戸支店", "0781234567", "0781234568",
                   "6500011", "兵庫県神戸市中央区下山手通4-5-6"),
        ],
    ),
    Company(
        name="株式会社グローバルメディア",
        domain="global-media.co.jp",
        hq_phone="0354445555",
        hq_fax="0354445556",
        generic_email_local="inquiry",
        hq_postal_code="1500011",
        hq_address="東京都渋谷区東1-2-3渋谷東ビル",
        branches=[
            Branch("関西支社", "0666667777", "0666667778",
                   "5300001", "大阪府大阪市北区梅田1-2-3"),
        ],
    ),
    Company(
        name="未来開発",
        domain="mirai-dev.com",
        hq_phone="0455556666",
        hq_fax="0455556667",
        generic_email_local="admin",
        hq_postal_code="2200005",
        hq_address="神奈川県横浜市西区南幸1-2-3未来ビル",
        branches=[
            Branch("東京オフィス", "0367778888", "0367778889",
                   "1500043", "東京都渋谷区道玄坂2-3-4"),
        ],
    ),
    Company(
        name="株式会社東海フード",
        domain="tokai-food.co.jp",
        hq_phone="0521119999",
        hq_fax="0521119990",
        generic_email_local="reception",
        hq_postal_code="4640084",
        hq_address="愛知県名古屋市千種区池下1-2-3東海ビル",
        branches=[
            Branch("豊橋工場", "0532112233", "0532112234",
                   "4400056", "愛知県豊橋市羽根井町1-2-3"),
            Branch("浜松営業所", "0531234567", "0531234568",
                   "4300929", "静岡県浜松市中区中央2-3-4"),
        ],
    ),
]

# プール3：部署（5 カテゴリ計 30 件）
DEPARTMENTS: List[str] = [
    # 営業系
    "営業部", "営業1課", "営業2課", "首都圏営業部", "関西営業所", "海外営業部",
    # 技術系
    "技術部", "開発部", "研究開発部", "ITインフラ部", "システム企画部",
    # 管理系
    "総務部", "人事部", "経理部", "IT推進部", "法務部", "財務部",
    # 製造系
    "製造部", "製造1課", "生産管理部", "品質管理部", "品質保証部",
    # その他
    "企画部", "経営企画室", "社長室", "購買部", "物流部", "広報部", "マーケティング部",
    "コーポレート本部",
]

# プール4：役職（階層 7 段階）
TITLES: List[str] = [
    "",  # 平社員（空文字）
    "主任",
    "係長",
    "課長",
    "次長",
    "部長",
    "取締役",
]


# =====================================================================
# Helper 関数群
# =====================================================================


def _romaji_join(parts: Iterable[str], sep: str = ".") -> str:
    """ローマ字を結合してメール local 部を作る。出力は半角小文字（§15.5.3）。"""
    return sep.join(parts).lower()


def _gen_personal_email_local(
    rng: random.Random, romaji_first: str, romaji_last: str
) -> str:
    """個人用メール local 部のバリエーション生成。値は既に小文字。"""
    pattern = rng.choice(["dot", "concat", "lastnum", "firstlast_us"])
    if pattern == "dot":
        return _romaji_join([romaji_first, romaji_last], ".")
    if pattern == "concat":
        return f"{romaji_first}{romaji_last}".lower()
    if pattern == "lastnum":
        return f"{romaji_last}{rng.randint(1, 99)}".lower()
    # firstlast_us
    return f"{romaji_first}_{romaji_last}".lower()


def _gen_generic_email_local(rng: random.Random, base: str) -> str:
    """代表メール local 部（§8.7 のハイフン/アンダースコア/ドット付きバリエーションを含む）。"""
    pattern = rng.choice(["plain", "hyphen", "underscore", "dot", "num"])
    if pattern == "plain":
        return base
    if pattern == "hyphen":
        return f"{base}-{rng.choice(['jp', 'tokyo', 'osaka', 'team'])}"
    if pattern == "underscore":
        return f"{base}_{rng.choice(['team', 'jp', 'sales', 'support'])}"
    if pattern == "dot":
        return f"{base}.{rng.choice(['jp', 'tokyo', 'osaka', 'main'])}"
    # num
    return f"{base}{rng.randint(2, 9)}"


def _gen_mobile(rng: random.Random) -> str:
    """携帯番号（070/080/090 始まり、数字のみ 11 桁、§15.5.3）。"""
    head = rng.choice(["070", "080", "090"])
    body = "".join(str(rng.randint(0, 9)) for _ in range(8))
    return f"{head}{body}"


def _resolve_confidence(
    rng: random.Random,
    field_name: str,
    mode: str,
    custom_high_fields: List[str],
) -> str:
    """フィールドの confidence を決定する（'low' / 'mid' / 'high'）。

    [性質] 純関数（rng は外部状態）
    """
    if mode == "high":
        return "high"
    if mode == "custom" and field_name in custom_high_fields:
        return "high"
    # random or custom の指定外フィールド
    dist = CONFIDENCE_DISTRIBUTION.get(field_name)
    if dist is None:
        return "high"
    high_p, mid_p, _low_p = dist
    r = rng.random()
    if r < high_p:
        return "high"
    if r < high_p + mid_p:
        return "mid"
    return "low"


# =====================================================================
# 名前・会社・部署プールの取得 helper
# =====================================================================


@dataclass
class FullName:
    """フルネーム（漢字 + ローマ字）。"""

    last_kanji: str
    last_romaji: str
    first_kanji: str
    first_romaji: str
    is_male: bool

    @property
    def full(self) -> str:
        return f"{self.last_kanji}{self.first_kanji}"

    @property
    def last(self) -> str:
        return self.last_kanji

    @property
    def first(self) -> str:
        return self.first_kanji


def _build_full_name(
    last: Tuple[str, str], first: Tuple[str, str], is_male: bool
) -> FullName:
    return FullName(
        last_kanji=last[0],
        last_romaji=last[1],
        first_kanji=first[0],
        first_romaji=first[1],
        is_male=is_male,
    )


def _pick_full_name(
    rng: random.Random,
    used_full: set,
    forced_last: Optional[Tuple[str, str]] = None,
    forced_first: Optional[Tuple[str, str]] = None,
    is_male: Optional[bool] = None,
) -> FullName:
    """同姓同名を避けて FullName を 1 つ取得（forced 指定があればそれを優先）。"""
    for _ in range(200):
        last = forced_last or rng.choice(LAST_NAMES)
        if is_male is None:
            male = rng.random() < 0.5
        else:
            male = is_male
        first_pool = MALE_FIRST_NAMES if male else FEMALE_FIRST_NAMES
        first = forced_first or rng.choice(first_pool)
        fn = _build_full_name(last, first, male)
        if fn.full not in used_full:
            return fn
        # forced 強制時はループしても同一値しか出ないので強制利用
        if forced_last is not None and forced_first is not None:
            return fn
    raise RuntimeError("名前プールから一意な氏名を生成できませんでした")


# =====================================================================
# Contact データ生成（DB 書込前のスナップショット）
# =====================================================================


@dataclass
class ContactSnapshot:
    """1 件の Contact + ContactFieldConfidence 用データのスナップショット。

    生成ロジックの結果を保持し、create_contact() で DB に書く / dry-run で表示する。
    """

    group_label: str
    full_name_obj: FullName
    company: Company  # Company dataclass インスタンス（Contact.organization の入力源）
    branch: Optional[Branch]
    department: str
    title: str
    email: str
    personal_phone: str  # Contact.personal_phone（JSONField）への書込時に [value] でラップ
    mobile_phone: str
    personal_fax: str    # Contact.personal_fax（JSONField）への書込時に [value] でラップ
    address: str
    postal_code: str
    confidence_map: dict  # field_name -> 'high'/'mid'/'low'

    @property
    def company_name(self) -> str:
        return self.company.name

    @property
    def branch_name(self) -> str:
        return self.branch.name if self.branch else ""


def _build_confidence_map(
    rng: random.Random, mode: str, custom_high_fields: List[str]
) -> dict:
    """confidence_map（field_name -> 'high'/'mid'/'low'）を生成。"""
    return {
        fn: _resolve_confidence(rng, fn, mode, custom_high_fields)
        for fn in TRACKED_CONFIDENCE_FIELDS
    }


def _maybe_apply_irregular(
    rng: random.Random, snap: ContactSnapshot, irregular_rate: float
) -> None:
    """イレギュラーパターンを混入（実値の差分注入）。

    確率 irregular_rate で snap の (personal_phone / address / postal_code) のいずれかを
    別値に差し替える。グループ① には適用しない（呼び出し側で除外）。
    """
    if rng.random() >= irregular_rate:
        return
    target = rng.choice(["phone", "address_postal", "email_domain"])
    if target == "phone":
        # 別の拠点の電話番号と入れ替える（あれば）
        # 注：Branch/Company 側の hq_phone / phone はそのまま（会社側の概念）。
        candidates = [snap.company.hq_phone] + [b.phone for b in snap.company.branches]
        choices = [p for p in candidates if p != snap.personal_phone]
        if choices:
            snap.personal_phone = rng.choice(choices)
    elif target == "address_postal":
        # 「郵便番号と住所が古い情報のまま」想定：同じ会社内で別の拠点 / 本社へ住所を差し替え
        # （例：本社→大阪支店、大阪支店→本社）。別会社の住所は混ぜない。
        candidates = [(snap.company.hq_postal_code, snap.company.hq_address)]
        candidates += [(b.postal_code, b.address) for b in snap.company.branches]
        choices = [(p, a) for p, a in candidates if a != snap.address]
        if choices:
            new_postal, new_address = rng.choice(choices)
            snap.postal_code = new_postal
            snap.address = new_address
    else:
        # email のドメインをフリーメールに差し替え（個人利用の Gmail を会社用にしている等）
        local = snap.email.split("@", 1)[0]
        domain = rng.choice(FREE_EMAIL_DOMAINS)
        snap.email = f"{local}@{domain}".lower()


def _make_personal_contact_snap(
    rng: random.Random,
    group_label: str,
    full_name: FullName,
    company: Company,
    department: str,
    title: str,
    *,
    branch: Optional[Branch] = None,
    mobile_phone: Optional[str] = None,
    use_generic_email: bool = False,
    confidence_mode: str = "random",
    custom_high_fields: Optional[List[str]] = None,
) -> ContactSnapshot:
    """1 件分の ContactSnapshot を組み立てる（DB に書かない）。

    branch=None は「本社」を意味する（branch をランダムに選びたい場合は呼び出し側で
    `rng.choice(company.branches)` を渡すこと）。

    v1.6.0 リネーム対応：snap の personal_phone / personal_fax には拠点 or 本社の番号を
    そのまま割り当てる（Phase 1B 以降に org_phone / org_fax が追加されたら役割を整理予定）。
    """
    if mobile_phone is None:
        mobile_phone = _gen_mobile(rng)

    if branch:
        personal_phone = branch.phone
        personal_fax = branch.fax
        postal_code = branch.postal_code
        address = branch.address
    else:
        personal_phone = company.hq_phone
        personal_fax = company.hq_fax
        postal_code = company.hq_postal_code
        address = company.hq_address

    if use_generic_email:
        local = _gen_generic_email_local(rng, company.generic_email_local)
        email = f"{local}@{company.domain}".lower()
    else:
        local = _gen_personal_email_local(rng, full_name.first_romaji, full_name.last_romaji)
        email = f"{local}@{company.domain}".lower()

    confidence_map = _build_confidence_map(
        rng, confidence_mode, custom_high_fields or []
    )

    return ContactSnapshot(
        group_label=group_label,
        full_name_obj=full_name,
        company=company,
        branch=branch,
        department=department,
        title=title,
        email=email,
        personal_phone=personal_phone,
        mobile_phone=mobile_phone,
        personal_fax=personal_fax,
        address=address,
        postal_code=postal_code,
        confidence_map=confidence_map,
    )


# =====================================================================
# グループ別ジェネレータ
# =====================================================================


def _generate_clone_group(
    rng: random.Random,
    n: int,
    confidence_mode: str,
    custom_high_fields: List[str],
) -> List[ContactSnapshot]:
    """グループ① 完全同一名刺（山田太郎、N 枚スキャン）。

    全フィールド一致。irregular は適用しない（呼び出し側で除外）。
    confidence のみ Contact ごとに違いがありうる（OCR の判定差を再現）。
    """
    full_name = _build_full_name(("山田", "yamada"), ("太郎", "taro"), True)
    company = COMPANIES[0]  # 株式会社明治テック
    branch = None  # 本社固定
    department = "営業部"
    title = "課長"
    mobile_phone = "09011112222"
    # メール local も固定（個人メール）
    email_local = _romaji_join([full_name.first_romaji, full_name.last_romaji], ".")
    base_email = f"{email_local}@{company.domain}".lower()
    base_phone = company.hq_phone
    base_fax = company.hq_fax
    base_postal = company.hq_postal_code
    base_address = company.hq_address

    snaps: List[ContactSnapshot] = []
    for _ in range(n):
        confidence_map = _build_confidence_map(rng, confidence_mode, custom_high_fields)
        snaps.append(
            ContactSnapshot(
                group_label="clone",
                full_name_obj=full_name,
                company=company,
                branch=branch,
                department=department,
                title=title,
                email=base_email,
                personal_phone=base_phone,
                mobile_phone=mobile_phone,
                personal_fax=base_fax,
                address=base_address,
                postal_code=base_postal,
                confidence_map=confidence_map,
            )
        )
    return snaps


def _generate_career_group(
    rng: random.Random,
    n: int,
    confidence_mode: str,
    custom_high_fields: List[str],
) -> List[ContactSnapshot]:
    """グループ② 異動・転職（鈴木花子、A→B→C→D を循環）。

    A: 同一会社内の異動（部署変更、メール命名規則維持）
    B: 同一会社内の昇進（役職のみ変更）
    C: 転職（会社・部署・メール・固定電話変更、携帯維持）
    D: 出向（会社・部署変更、携帯も変更、フルネーム維持）
    """
    full_name = _build_full_name(("鈴木", "suzuki"), ("花子", "hanako"), False)
    company_a = COMPANIES[1]  # 昭和商事
    company_c = COMPANIES[3]  # 令和ソフトウェア（転職先）
    company_d = COMPANIES[6]  # 日本物流（出向先）
    mobile_persist = "08033334444"  # 携帯（A〜C は維持）
    mobile_after_d = "08055556666"  # D で携帯も変わる

    patterns = ["A", "B", "C", "D"]
    snaps: List[ContactSnapshot] = []
    for i in range(n):
        p = patterns[i % 4]
        if p == "A":
            # 同一会社（昭和商事）内の異動：部署変更、メール命名規則維持
            snaps.append(
                _make_personal_contact_snap(
                    rng, "career_A", full_name, company_a,
                    department="営業部", title="主任",
                    branch=None,  # 本社
                    mobile_phone=mobile_persist,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
        elif p == "B":
            # 同一会社内の昇進：役職のみ変更
            snaps.append(
                _make_personal_contact_snap(
                    rng, "career_B", full_name, company_a,
                    department="営業部", title="課長",
                    branch=None,
                    mobile_phone=mobile_persist,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
        elif p == "C":
            # 転職：会社変更、部署変更、メール命名規則は新会社ドメイン、固定電話変更、携帯維持
            snaps.append(
                _make_personal_contact_snap(
                    rng, "career_C", full_name, company_c,
                    department="技術部", title="課長",
                    branch=None,
                    mobile_phone=mobile_persist,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
        else:  # D
            # 出向：会社・部署変更、携帯も変更、フルネーム維持
            snaps.append(
                _make_personal_contact_snap(
                    rng, "career_D", full_name, company_d,
                    department="物流部", title="部長",
                    branch=None,
                    mobile_phone=mobile_after_d,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
    return snaps


def _generate_namechange_group(
    rng: random.Random,
    n: int,
    confidence_mode: str,
    custom_high_fields: List[str],
) -> List[ContactSnapshot]:
    """グループ③ 改姓・部署変更（田中由美 → 佐藤由美 等、3 バリエーション循環）。

    Sub1: 姓だけ変わる、部署も会社も維持（メール命名規則変更）
    Sub2: 姓 + 部署変更
    Sub3: 姓 + 会社変更（離婚＋転職）

    携帯は不変（possible_high の閾値到達確認）。
    """
    name_old = _build_full_name(("田中", "tanaka"), ("由美", "yumi"), False)
    name_new = _build_full_name(("佐藤", "sato"), ("由美", "yumi"), False)
    company_old = COMPANIES[2]  # 平成電機
    company_new = COMPANIES[7]  # グローバルメディア
    mobile_persist = "09077778888"  # 携帯維持

    patterns = ["S1", "S2", "S3"]
    snaps: List[ContactSnapshot] = []
    for i in range(n):
        p = patterns[i % 3]
        if p == "S1":
            # 姓だけ変わる、会社・部署維持
            snaps.append(
                _make_personal_contact_snap(
                    rng,
                    "namechange_S1" if i == 0 else f"namechange_S1_{i}",
                    name_old if i == 0 else name_new,
                    company_old,
                    department="技術部", title="主任",
                    branch=None,
                    mobile_phone=mobile_persist,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
        elif p == "S2":
            # 姓 + 部署変更
            snaps.append(
                _make_personal_contact_snap(
                    rng, "namechange_S2", name_new, company_old,
                    department="開発部", title="課長",
                    branch=None,
                    mobile_phone=mobile_persist,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
        else:  # S3
            # 姓 + 会社変更
            snaps.append(
                _make_personal_contact_snap(
                    rng, "namechange_S3", name_new, company_new,
                    department="マーケティング部", title="課長",
                    branch=None,
                    mobile_phone=mobile_persist,
                    confidence_mode=confidence_mode,
                    custom_high_fields=custom_high_fields,
                )
            )
    return snaps


def _generate_namesake_group(
    rng: random.Random,
    n: int,
    confidence_mode: str,
    custom_high_fields: List[str],
) -> List[ContactSnapshot]:
    """グループ④ 同姓同名（佐藤健、N 人、会社・携帯・メール全部違う）。"""
    full_name = _build_full_name(("佐藤", "sato"), ("健", "ken"), True)

    # 異なる会社・部署・携帯を割り当て
    companies = [COMPANIES[4], COMPANIES[5], COMPANIES[8], COMPANIES[9], COMPANIES[0]]
    departments = ["製造部", "研究開発部", "営業1課", "総務部", "企画部"]
    titles = ["係長", "次長", "主任", "部長", "課長"]
    mobiles = [
        "08099990000", "07011112222", "09033334444", "08077778888", "07055556666",
    ]

    snaps: List[ContactSnapshot] = []
    for i in range(n):
        idx = i % len(companies)
        company = companies[idx]
        snaps.append(
            _make_personal_contact_snap(
                rng, "namesake", full_name, company,
                department=departments[idx],
                title=titles[idx],
                branch=None,
                mobile_phone=mobiles[idx],
                confidence_mode=confidence_mode,
                custom_high_fields=custom_high_fields,
            )
        )
    return snaps


# 他グループで使う氏名はノイズと衝突させないため、固定氏名を予約しておく。
RESERVED_FULL_NAMES = {"山田太郎", "鈴木花子", "田中由美", "佐藤由美", "佐藤健"}


def _generate_noise_group(
    rng: random.Random,
    n: int,
    confidence_mode: str,
    custom_high_fields: List[str],
) -> List[ContactSnapshot]:
    """グループ⑤ ノイズ（全く似ていない人を N 人）。

    絞り込みアルゴリズム（§8.10）が正しく除外することを確認するため、
    可能な限り氏名・会社・携帯・メールを散らす。
    """
    snaps: List[ContactSnapshot] = []
    used_full = set(RESERVED_FULL_NAMES)
    for _ in range(n):
        full_name = _pick_full_name(rng, used_full)
        used_full.add(full_name.full)
        company = rng.choice(COMPANIES)
        department = rng.choice(DEPARTMENTS)
        title = rng.choice(TITLES)
        # ノイズの 30% は代表メール、残りは個人メール
        use_generic = rng.random() < 0.3
        snaps.append(
            _make_personal_contact_snap(
                rng, "noise", full_name, company,
                department=department,
                title=title,
                branch=rng.choice(company.branches) if (company.branches and rng.random() < 0.5) else None,
                mobile_phone=_gen_mobile(rng),
                use_generic_email=use_generic,
                confidence_mode=confidence_mode,
                custom_high_fields=custom_high_fields,
            )
        )
    return snaps


# =====================================================================
# DB 書込
# =====================================================================


def _create_contact_from_snapshot(
    snap: ContactSnapshot, user
) -> Tuple[Person, Contact]:
    """ContactSnapshot から Person + Contact + ContactFieldConfidence を DB 作成。

    [性質] 副作用あり（DB 書込）
    [入力] snap: ContactSnapshot, user: created_by/updated_by に使う User
    [出力] (Person, Contact)

    ContactFieldConfidence は §10.6.4 ケース 1（高 = レコード作らない）方針で、
    confidence='high' のフィールドはレコードを作成しない。
    """
    person = Person.objects.create(status=Person.Status.ACTIVE)

    # v1.6.0：personal_phone / personal_fax は JSONField(default=list)。
    # 値があれば 1 要素リストに、空文字なら空リストにラップする。
    personal_phone_list = [snap.personal_phone] if snap.personal_phone else []
    personal_fax_list = [snap.personal_fax] if snap.personal_fax else []

    contact = Contact.objects.create(
        business_card=None,
        person=person,
        status=Contact.Status.PRIMARY,
        created_by=user,
        updated_by=user,
        full_name=snap.full_name_obj.full,
        last_name=snap.full_name_obj.last,
        first_name=snap.full_name_obj.first,
        organization=snap.company_name,
        branch=snap.branch_name,
        department=snap.department,
        title=snap.title,
        email=snap.email,
        personal_phone=personal_phone_list,
        mobile_phone=snap.mobile_phone,
        personal_fax=personal_fax_list,
        address=snap.address,
        postal_code=snap.postal_code,
    )

    # primary_contact 派生情報の同期
    person.primary_contact = contact
    person.save(update_fields=["primary_contact", "updated_at"])

    # ContactFieldConfidence：low / mid のみ DB レコード化（§4.6.1 / §10.6.4）
    ContactFieldConfidence.create_for_contact(contact, snap.confidence_map)

    return person, contact


# =====================================================================
# Reset
# =====================================================================


def _reset_test_data(user, stdout) -> dict:
    """test_data_user 由来の Contact / Person / ContactFieldConfidence を全削除。

    [性質] 副作用あり（複合処理: DB 削除）
    [入力] user: User（test_data_user）, stdout: 進捗用
    [出力] dict (contacts: 削除した Contact 数, persons: 削除した孤児 Person 数,
             confidences: CASCADE で削除された ContactFieldConfidence 数)
    """
    affected_person_ids = set(
        Contact.objects.filter(created_by=user).values_list("person_id", flat=True)
    )

    # delete() は (total, per_model_dict) を返す。per_model_dict から純粋な件数を取り出す。
    _total, per_model = Contact.objects.filter(created_by=user).delete()
    deleted_contacts = per_model.get(Contact._meta.label, 0)
    deleted_cfc = per_model.get(ContactFieldConfidence._meta.label, 0)

    deleted_persons = 0
    for pid in affected_person_ids:
        if not Contact.objects.filter(person_id=pid).exists():
            # primary_contact は SET_NULL 済み。Person を物理削除。
            Person.objects.filter(id=pid).delete()
            deleted_persons += 1

    stdout.write(
        f"  reset: deleted contacts={deleted_contacts}, "
        f"cfc cascaded={deleted_cfc}, orphan persons={deleted_persons}"
    )
    return {
        "contacts": deleted_contacts,
        "persons": deleted_persons,
        "confidences": deleted_cfc,
    }


# =====================================================================
# 表示 helper
# =====================================================================


def _format_snap_line(idx: int, snap: ContactSnapshot, verbose: bool) -> str:
    """1 件分の Contact を表示用文字列に。"""
    confidence_low_mid = sorted(
        f"{k}={v}" for k, v in snap.confidence_map.items() if v in ("low", "mid")
    )
    short = (
        f"  [{idx:03d}][{snap.group_label:>14s}] "
        f"{snap.full_name_obj.full:<10s} | {snap.company_name} / {snap.department} "
        f"/ {snap.title or '(平)'} | mobile_phone={snap.mobile_phone} email={snap.email}"
    )
    if not verbose:
        return short
    detail = (
        f"\n         branch={snap.branch_name or '-'} personal_phone={snap.personal_phone} "
        f"personal_fax={snap.personal_fax} postal={snap.postal_code} address={snap.address}"
        f"\n         confidence(low/mid)={','.join(confidence_low_mid) or '-'}"
    )
    return short + detail


# =====================================================================
# Command
# =====================================================================


class Command(BaseCommand):
    help = (
        "v1.4.2 系統X / 系統Y / C ブロック動作確認用のテストデータを生成する開発ツール。"
        "Contact + Person + ContactFieldConfidence のみ生成（BusinessCard / OriginalImage は作らない）。"
    )

    def add_arguments(self, parser):
        parser.add_argument("--clone", type=int, default=5,
                            help="グループ① 完全同一名刺の件数（デフォルト 5）")
        parser.add_argument("--career", type=int, default=4,
                            help="グループ② 異動・転職の件数（デフォルト 4）")
        parser.add_argument("--namechange", type=int, default=3,
                            help="グループ③ 改姓・部署変更の件数（デフォルト 3）")
        parser.add_argument("--namesake", type=int, default=2,
                            help="グループ④ 同姓同名の件数（デフォルト 2）")
        parser.add_argument("--noise", type=int, default=10,
                            help="グループ⑤ ノイズの件数（デフォルト 10）")
        parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                            help="ランダム再現用シード（デフォルト 42）")
        parser.add_argument(
            "--confidence-mode", choices=["random", "high", "custom"], default="random",
            help="ContactFieldConfidence の生成モード（random / high / custom）",
        )
        parser.add_argument(
            "--high-fields", type=str, default="",
            help="custom モード時に high 扱いするフィールド名（カンマ区切り）",
        )
        parser.add_argument("--reset", action="store_true",
                            help="既存テストデータ（test_data_user 由来）を削除してから生成")
        parser.add_argument("--irregular-rate", type=float, default=0.1,
                            help="イレギュラー混入率（0.0〜1.0、デフォルト 0.1）")
        parser.add_argument("--dry-run", action="store_true",
                            help="DB に書かず内容を表示するだけ")
        parser.add_argument("--verbose", action="store_true",
                            help="詳細表示（住所・電話・confidence 詳細）")

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理: DB 書込・削除・stdout 出力）

        [入力] CLI オプション一式
        [出力] None（実行サマリを stdout に出力）
        [例外] CommandError（test_data_user が存在しない / 引数不正）
        """
        clone_n = options["clone"]
        career_n = options["career"]
        namechange_n = options["namechange"]
        namesake_n = options["namesake"]
        noise_n = options["noise"]
        seed = options["seed"]
        confidence_mode = options["confidence_mode"]
        high_fields_str = options["high_fields"]
        reset_flag = options["reset"]
        irregular_rate = options["irregular_rate"]
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        for name, value in [
            ("--clone", clone_n), ("--career", career_n),
            ("--namechange", namechange_n), ("--namesake", namesake_n),
            ("--noise", noise_n),
        ]:
            if value < 0:
                raise CommandError(f"{name} は 0 以上で指定してください: {value}")
        if not (0.0 <= irregular_rate <= 1.0):
            raise CommandError(f"--irregular-rate は 0.0〜1.0: {irregular_rate}")

        custom_high_fields: List[str] = []
        if confidence_mode == "custom":
            if not high_fields_str:
                raise CommandError(
                    "--confidence-mode custom には --high-fields でフィールド名を "
                    "カンマ区切り指定してください"
                )
            custom_high_fields = [f.strip() for f in high_fields_str.split(",") if f.strip()]
            invalid = [
                f for f in custom_high_fields if f not in TRACKED_CONFIDENCE_FIELDS
            ]
            if invalid:
                raise CommandError(
                    f"--high-fields に未知のフィールドが含まれます: {invalid}. "
                    f"指定可能: {TRACKED_CONFIDENCE_FIELDS}"
                )

        # 1. test_data_user 取得（dry-run 以外で必須、無ければエラー終了）
        user = None
        if not dry_run:
            try:
                user = User.objects.get(username=TEST_USER_USERNAME)
            except User.DoesNotExist:
                raise CommandError(
                    f"ユーザー '{TEST_USER_USERNAME}' が存在しません。Django Admin で "
                    f"事前作成してから実行してください。本コマンドはユーザーを作成しません。"
                )

        # 2. snapshot 生成（DB に書かない）
        rng = random.Random(seed)
        snaps: List[ContactSnapshot] = []
        snaps += _generate_clone_group(rng, clone_n, confidence_mode, custom_high_fields)
        clone_count = clone_n
        snaps += _generate_career_group(rng, career_n, confidence_mode, custom_high_fields)
        snaps += _generate_namechange_group(rng, namechange_n, confidence_mode, custom_high_fields)
        snaps += _generate_namesake_group(rng, namesake_n, confidence_mode, custom_high_fields)
        snaps += _generate_noise_group(rng, noise_n, confidence_mode, custom_high_fields)

        # 3. irregular 混入（グループ① 以外）
        for snap in snaps[clone_count:]:
            _maybe_apply_irregular(rng, snap, irregular_rate)

        # 4. 表示（dry-run でも実行時でも常に出す）
        self.stdout.write(self.style.NOTICE(
            f"--- dev_create_test_contact_data: seed={seed}, mode={confidence_mode}, "
            f"irregular_rate={irregular_rate}, dry_run={dry_run} ---"
        ))
        for i, snap in enumerate(snaps, start=1):
            self.stdout.write(_format_snap_line(i, snap, verbose=verbose))

        group_counts = {
            "clone": clone_n,
            "career": career_n,
            "namechange": namechange_n,
            "namesake": namesake_n,
            "noise": noise_n,
        }
        total = sum(group_counts.values())
        self.stdout.write(self.style.NOTICE(
            f"  group counts: {group_counts}  total={total}"
        ))

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"dry-run: would create {total} contacts (DB 未変更)"
            ))
            return

        # 5. reset（指示時のみ）と DB 書込
        with transaction.atomic():
            if reset_flag:
                _reset_test_data(user, self.stdout)
            created_persons = 0
            created_contacts = 0
            created_cfc = 0
            for snap in snaps:
                _, contact = _create_contact_from_snapshot(snap, user)
                created_persons += 1
                created_contacts += 1
                created_cfc += ContactFieldConfidence.objects.filter(
                    contact=contact
                ).count()

        self.stdout.write(self.style.SUCCESS(
            f"created: persons={created_persons}, contacts={created_contacts}, "
            f"contact_field_confidences={created_cfc}"
        ))
