"""EmailContext と prepare ファクトリ（仕様書 v1.6 §7.4、rev14）。

メール本文の生成（差し込み変数展開・カスタムタグ展開・プレーンテキスト→HTML 変換）の
司令塔となる DTO とそのファクトリ。三層責務（§7.4.3）：

  司令塔        ：EmailContext.prepare（classmethod、本ファイル）
  mode 分岐     ：_render_custom_tags_*（モジュール非公開関数、本ファイル）
  レコード組立  ：TrackingLink.build() / UnsubscribeLink.build()（モデル classmethod、§7.4.3）

mode 3 値（EmailMode、§7.4.2）：

  PRODUCTION ：本番送信。トークン発行（build を呼ぶ）、リンクは {site}/t/<token>/
  TEST       ：テスト配信。トークン非発行、リンクは素 URL
  PREVIEW    ：プレビュー。同上

処理順は §7.4.4.5 厳守（リンク壊れ事故防止）：
  1. _render_merge_field（差し込み 18 変数の生値置換、HTML エスケープしない）
  2. プレーンテキスト全体を HTML エスケープ + 改行 → <br>
  3. カスタムタグ {% tracked_link %} / {% unsubscribe_link %} を <a> に変換

逆順厳禁：先にカスタムタグを <a> 化してから全体エスケープすると、<a> が &lt;a&gt; 化して
全リンクが死ぬ重大事故（rev10 で是正済み）。
"""

from __future__ import annotations

import enum
import html
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config.constants import MAILING_SITE_URL

if TYPE_CHECKING:
    from mailings.models import Campaign, TrackingLink, UnsubscribeLink
    from persons.models import Person


class EmailMode(enum.Enum):
    """配信 mode（§7.4.2、3 値）。

    文字列リテラルの直書き比較を禁止し、mode 識別子を 1 箇所に集約する。
    各所（prepare / 3 処理単位 / mode 分岐関数 / テスト配信 / プレビュー View）は
    本 enum を import して参照する。表記揺れで「テスト配信なのにトークン発行」等の
    事故を構造的に防ぐ（rev10 で確定）。
    """

    PRODUCTION = "本番送信"
    TEST = "テスト配信"
    PREVIEW = "プレビュー"


# 差し込み 18 変数の確定対応表（仕様書 §7.4.4.3.1、rev13 で 16→18 に拡張）。
# (記法, contact 属性名 or None) のタプル。差出人系は contact 属性ではないため別扱い。
_MERGE_FIELDS_CONTACT: tuple[tuple[str, str], ...] = (
    ("{{会社名}}", "organization"),
    ("{{部署}}", "department"),
    ("{{役職}}", "title"),
    ("{{支店}}", "branch"),
    ("{{フルネーム}}", "full_name"),
    ("{{姓}}", "last_name"),
    ("{{名}}", "first_name"),
    ("{{宛名}}", "salutation_name"),
    ("{{住所}}", "address"),
    ("{{郵便番号}}", "postal_code"),
    ("{{メール}}", "email"),
    ("{{個人電話}}", "personal_phone"),
    ("{{会社電話}}", "org_phone"),
    ("{{携帯}}", "mobile_phone"),
    ("{{個人FAX}}", "personal_fax"),
    ("{{会社FAX}}", "org_fax"),
)


def _render_merge_field(body: str, contact, sender) -> str:
    """差し込み変数を生値置換する（§7.4.4、prepare 専用、モジュール非公開）。

    [性質] 純関数（DB 操作なし、文字列処理のみ）
    [入力] body: str（テンプレ本文）、contact: Contact、sender: CustomUser
    [出力] str（置換後の本文）

    18 変数（§7.4.4.3.1）。値が空（None/空文字）なら記法をそのまま残す（Sansan 互換、§7.4.5）。
    HTML エスケープは行わない（プレーンテキストのまま）。エスケープは prepare の処理順
    ステップ 2 で全体に一括適用する（§7.4.4.5）。
    """
    for tag, attr in _MERGE_FIELDS_CONTACT:
        value = getattr(contact, attr, "") or ""
        if value:
            body = body.replace(tag, value)
    sender_full_name = sender.get_full_name() if sender else ""
    if sender_full_name:
        body = body.replace("{{差出人の氏名}}", sender_full_name)
    sender_signature = getattr(sender, "signature", "") if sender else ""
    if sender_signature:
        # signature 内に差し込み変数があっても再帰展開しない（プレーン固定文字列、§7.4.4.3.1）。
        body = body.replace("{{差出人の署名}}", sender_signature)
    return body


def _escape_and_brify(body: str) -> str:
    """プレーンテキスト全体を HTML エスケープし、改行を <br> に変換する（§7.4.4.5 ②）。

    [性質] 純関数
    [入力] body: str
    [出力] str（HTML 化済み）

    `<`/`>`/`&`/`"`/`'` をすべてエスケープ（仕様書 §7.4.4.5 ステップ ②「HTML 特殊文字
    `<`／`>`／`&`／`"` 等をすべてエスケープ」を厳守、quote=True）。

    ステップ ③ のカスタムタグ正規表現は本ステップ後の本文（`"` → `&quot;` 済み）を
    前提に書かれており、`&quot;` で括られた URL を拾う。URL 内の `&` も本ステップで
    `&amp;` 化されるため、③ で抽出後に `html.unescape` で生 URL に戻して TrackingLink
    に保存する。本番送信時の最終 href は `{site}/t/{token}/` でユーザー入力 URL を
    属性値に直接置かないため、 `"` が紛れ込んで属性破壊する経路を構造的に断つ。
    テスト・プレビュー時は元 URL を href に置くため、`html.escape(url, quote=True)` で
    再エスケープしてから埋め込む。
    """
    escaped = html.escape(body, quote=True)
    return escaped.replace("\r\n", "<br>").replace("\n", "<br>")


# カスタムタグの正規表現（§7.4.6、2 形態ずつ）。
# 重要：本正規表現は _escape_and_brify（ステップ ②）の後の本文に対して適用される前提。
# `"` は `&quot;` に、URL 内の `&` は `&amp;` にエスケープされているため、ユーザー記法の
# `{% tracked_link "URL" %}` は実際には `{% tracked_link &quot;URL&quot; %}` となっている。
_RE_TRACKED_LINK_WITH_URL = re.compile(
    r"\{%\s*tracked_link\s+&quot;(.+?)&quot;\s*%\}(.*?)\{%\s*endtracked_link\s*%\}",
    re.DOTALL,
)
# tracked_link 形態 1：{% tracked_link %}URL 文字列{% endtracked_link %}（中身が URL かつリンクテキスト）。
# 中身の URL も ② で `&` が `&amp;` 化されている可能性があるため、抽出後に unescape する。
_RE_TRACKED_LINK_BARE = re.compile(
    r"\{%\s*tracked_link\s*%\}(.+?)\{%\s*endtracked_link\s*%\}",
    re.DOTALL,
)
# unsubscribe_link 形態 2：{% unsubscribe_link %}テキスト{% endunsubscribe_link %}
# （記法そのものに `"` を含まないため ② の影響を受けない）
_RE_UNSUBSCRIBE_LINK_WITH_TEXT = re.compile(
    r"\{%\s*unsubscribe_link\s*%\}(.*?)\{%\s*endunsubscribe_link\s*%\}",
    re.DOTALL,
)
# unsubscribe_link 形態 1：{% unsubscribe_link %}（単独、システム既定文言）
_RE_UNSUBSCRIBE_LINK_BARE = re.compile(r"\{%\s*unsubscribe_link\s*%\}")

UNSUBSCRIBE_DEFAULT_LABEL = "配信停止"


def _track_url(token: str) -> str:
    return f"{MAILING_SITE_URL}/t/{token}/"


def _unsubscribe_url(token: str) -> str:
    return f"{MAILING_SITE_URL}/u/{token}/"


def _unsubscribe_url_tokenless() -> str:
    """テスト・プレビュー用の配信停止案内 URL（§4.5A.2.1、トークン無し独立ルート）。"""
    return f"{MAILING_SITE_URL}/u/"


def _render_custom_tags_production(
    body: str,
    campaign,
    person,
    to_email: str,
    tracking_links: list,
    unsubscribe_links: list,
) -> str:
    """PRODUCTION mode：記法を build() でトークン付きリンクに変換（§7.4.6.4）。

    [性質] 副作用あり（純関数引数の tracking_links/unsubscribe_links に append、
           build 自体は DB 書き込みなし）
    [入力] body: HTML 化済み本文（escape + <br> 適用済み、`"`→`&quot;`、URL 内 `&`→`&amp;`）
           to_email: 送信先メアド（UnsubscribeLink.target_email に焼き込む、rev15 §4.5A）
    [出力] str（<a> タグに変換済み）

    抽出した URL は ② でエスケープ済みなので `html.unescape` で生 URL に戻して
    TrackingLink.original_url に保存する（DB には生 URL）。本番送信時の最終 href は
    `{site}/t/{token}/` でユーザー入力 URL を直接 href に置かないため、属性値破壊の
    経路が構造的に存在しない。リンクテキスト部分は ② でエスケープ済みのまま <a> 内に
    残す（二重エスケープ回避、XSS 防止は ② で完了している）。
    """
    from mailings.models import TrackingLink as TL
    from mailings.models import UnsubscribeLink as UL

    # tracked_link 形態 2 を先に処理（&quot;URL&quot; 付きが形態 1 にもマッチしてしまうため順序重要）。
    def _replace_tracked_with_url(match):
        url = html.unescape(match.group(1))  # &amp;→&, &quot;→" を生 URL に戻す
        text = match.group(2)  # ② でエスケープ済みのまま <a> 内に残す
        link = TL.build(campaign=campaign, person=person, original_url=url)
        tracking_links.append(link)
        return f'<a href="{_track_url(link.token)}">{text}</a>'

    body = _RE_TRACKED_LINK_WITH_URL.sub(_replace_tracked_with_url, body)

    def _replace_tracked_bare(match):
        url_escaped = match.group(1).strip()
        url = html.unescape(url_escaped)
        link = TL.build(campaign=campaign, person=person, original_url=url)
        tracking_links.append(link)
        # リンクテキスト表示は ② エスケープ済み文字列をそのまま使う（二重エスケープ回避）
        return f'<a href="{_track_url(link.token)}">{url_escaped}</a>'

    body = _RE_TRACKED_LINK_BARE.sub(_replace_tracked_bare, body)

    # 形態 2（テキスト指定）を先に処理して、形態 1（単独タグ）が拾ってしまわないようにする。
    def _replace_unsubscribe_with_text(match):
        text = match.group(1)
        link = UL.build(campaign=campaign, person=person, target_email=to_email)
        unsubscribe_links.append(link)
        return f'<a href="{_unsubscribe_url(link.token)}">{text}</a>'

    body = _RE_UNSUBSCRIBE_LINK_WITH_TEXT.sub(_replace_unsubscribe_with_text, body)

    def _replace_unsubscribe_bare(_match):
        # 同一キャンペーン × Person で複数の {% unsubscribe_link %} が現れた場合も
        # 1 つの UL レコードに集約（UniqueConstraint(campaign, person) と整合）。
        if unsubscribe_links:
            link = unsubscribe_links[0]
        else:
            link = UL.build(campaign=campaign, person=person, target_email=to_email)
            unsubscribe_links.append(link)
        return f'<a href="{_unsubscribe_url(link.token)}">{UNSUBSCRIBE_DEFAULT_LABEL}</a>'

    body = _RE_UNSUBSCRIBE_LINK_BARE.sub(_replace_unsubscribe_bare, body)
    return body


def _render_custom_tags_test_or_preview(body: str) -> str:
    """TEST/PREVIEW mode：記法を素リンクに変換（build を呼ばない、§7.4.6.4）。

    [性質] 純関数
    [入力] body: HTML 化済み本文（escape + <br> 適用済み、`"`→`&quot;`、URL 内 `&`→`&amp;`）
    [出力] str（素リンクの <a> タグに変換済み）

    テスト・プレビューは元 URL を href 属性に直接置くため、属性値破壊（URL 内 `"` で
    href が破綻する事故）を防ぐ目的で URL を `html.escape(quote=True)` で再エスケープ
    してから埋め込む。これは本番送信経路（href はトークン URL）と異なる扱い。
    """

    def _replace_with_url(match):
        url = html.unescape(match.group(1))
        text = match.group(2)
        return f'<a href="{html.escape(url, quote=True)}">{text}</a>'

    body = _RE_TRACKED_LINK_WITH_URL.sub(_replace_with_url, body)

    def _replace_bare(match):
        url_escaped = match.group(1).strip()
        url = html.unescape(url_escaped)
        return f'<a href="{html.escape(url, quote=True)}">{url_escaped}</a>'

    body = _RE_TRACKED_LINK_BARE.sub(_replace_bare, body)
    body = _RE_UNSUBSCRIBE_LINK_WITH_TEXT.sub(
        lambda m: f'<a href="{_unsubscribe_url_tokenless()}">{m.group(1)}</a>', body
    )
    body = _RE_UNSUBSCRIBE_LINK_BARE.sub(
        f'<a href="{_unsubscribe_url_tokenless()}">{UNSUBSCRIBE_DEFAULT_LABEL}</a>',
        body,
    )
    return body


def _render_subject(subject: str, contact, sender) -> str:
    """件名の差し込み展開（プレーンテキストのまま、HTML 化しない、§7.4.4.4）。

    [性質] 純関数
    [入力] subject: str、contact: Contact、sender: CustomUser
    [出力] str
    """
    return _render_merge_field(subject, contact, sender)


@dataclass(frozen=True)
class EmailContext:
    """受信者 1 人分のメール構成データ（仕様書 §7.4.1、DTO、frozen=True）。

    最小参照（B 案、§7.4.1.1）：直接持つのは campaign と person のみ。
    email_template は campaign.template、sender は campaign.created_by で辿る。
    frozen=True の目的は「共有単一素材の不変性」（§7.4.1.2）：prepare で組んだものが
    最終形であり、後段の誰も書き換えない。プレビュー≡送信のズレ防止。

    持つフィールド（§7.4.1.3）：
      - campaign / person 参照
      - from_email / from_name（campaign.created_by から導出）
      - to_email（テスト配信時は dataclasses.replace で差し替え可）
      - subject_rendered（差し込み展開済み件名）
      - body_html（差し込み展開 + HTML 化 + リンク変換済み本文）
      - tracking_links / unsubscribe_links（PRODUCTION のみ、未保存、後段が bulk_create）

    持たないもの：プレビュー画面用 HTML、送信後データ（DeliveryHistory 等）、
    差出ドメイン検証結果（prepare の責務外、§7.4.3.3）。

    唯一のメソッドは prepare classmethod。送信・検証・加工メソッドは持たない。
    """

    campaign: "Campaign"
    person: "Person"
    mode: EmailMode
    from_email: str
    from_name: str
    to_email: str
    subject_rendered: str
    body_html: str
    # PRODUCTION のみ非空、TEST/PREVIEW では常に空リスト（trackin/unsubscribe の build を呼ばない）。
    tracking_links: tuple["TrackingLink", ...] = field(default_factory=tuple)
    unsubscribe_links: tuple["UnsubscribeLink", ...] = field(default_factory=tuple)

    @classmethod
    def prepare(cls, campaign, person, mode: EmailMode) -> "EmailContext":
        """ファクトリ：1 受信者分の EmailContext を組み立てる（§7.4.2、三層責務の司令塔）。

        [性質] 準関数寄り（DB 読み取り：campaign.template / campaign.created_by /
               person.primary_contact 等。DB 書き込みなし）
        [入力] campaign: Campaign、person: Person、mode: EmailMode（必須）
        [出力] EmailContext（frozen=True、後段の bulk_create 用 build 済みリンクを抱える）

        処理順（§7.4.4.5）：
          1. _render_merge_field で差し込み 18 変数の生値置換（HTML エスケープしない）
          2. プレーンテキスト全体を HTML エスケープ + 改行 → <br>
          3. カスタムタグを <a> に変換（PRODUCTION なら build でトークン付き、
             TEST/PREVIEW なら素リンク）

        mode 分岐（§7.4.6.4）：
          - PRODUCTION：TrackingLink.build() / UnsubscribeLink.build() を呼びトークン発行
          - TEST/PREVIEW：build を呼ばず素 URL に変換、unsubscribe は /u/ 案内ルート
        """
        if not isinstance(mode, EmailMode):
            raise TypeError(
                f"mode must be EmailMode enum, got {type(mode).__name__}. "
                "文字列リテラルの直書きは禁止（§7.4.2）"
            )

        contact = person.primary_contact
        sender = campaign.created_by
        template = campaign.template

        # 件名：差し込みのみ（HTML 化しない、§7.4.4.4）。
        subject_rendered = _render_subject(template.subject, contact, sender)

        # to_email を先に確定（UnsubscribeLink.target_email へ焼き込むため、rev15 §4.5A）
        to_email = contact.email if contact else ""

        # 本文：処理順 ① 差し込み生値置換
        body = _render_merge_field(template.body, contact, sender)
        # 処理順 ② プレーンテキスト全体を HTML エスケープ + 改行 → <br>
        body = _escape_and_brify(body)
        # 処理順 ③ カスタムタグを <a> に変換
        tracking_links: list = []
        unsubscribe_links: list = []
        if mode is EmailMode.PRODUCTION:
            body = _render_custom_tags_production(
                body, campaign, person, to_email, tracking_links, unsubscribe_links
            )
        else:
            body = _render_custom_tags_test_or_preview(body)

        # From：sender_mode による分岐（§7.8.1）。
        if campaign.sender_mode == campaign.SenderMode.NEWSLETTER:
            from_email = campaign.sender_email
            from_name = campaign.sender_name
        else:
            from_email = sender.email
            from_name = sender.get_full_name()

        return cls(
            campaign=campaign,
            person=person,
            mode=mode,
            from_email=from_email,
            from_name=from_name,
            to_email=to_email,
            subject_rendered=subject_rendered,
            body_html=body,
            tracking_links=tuple(tracking_links),
            unsubscribe_links=tuple(unsubscribe_links),
        )
