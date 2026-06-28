"""機能横断で使う汎用バッジ用テンプレートタグ（HIG v1.4 原則4・3.8 が正本）。

status の内部英字値が各テンプレートに {% if %} で直書きされ、Contact / Person で
英語が露出していた問題を、ここに集約して解消する。

テンプレート側からは {% load badge_tags %} で読み込み、

  - {% status_badge instance %}              … choices フィールド用（既定 field="status"）
  - {% bool_badge value true_label=... %}    … BooleanField 用（ラベル・色は kwargs）

の形で呼び出す。実装書式（register・simple_tag・format_html・色マップ dict・
安全側 fallback）は cards/templatetags/ui_tags.py の流儀に倣う。

設計メモ：
  - 色（variant）はテンプレに直書きせず、本モジュールの色マップから引く。
  - 色マップは **(モデル名, フィールド名) の文脈込みキー** で引く。値だけのフラット辞書に
    しないのは、Contact.status の "active"（副コンタクト）と Person.status の "active"
    （通常）が同一文字列でも別の色を持つため。ui_tags の「フィールドごとに専用 dict を
    持つ」流儀を、この複合キー形で踏襲する。
  - fallback は必ず安全側：色マップ未登録の値・フィールドが来ても variant は muted（灰）に
    倒し、テキストは get_FIELD_display()（モデルラベル）を出す。生の英字値は画面に出さない。
  - variant は app.css に定義済みの 5 種（success / warning / error / muted / info）のみ。
    これ以外は _render_badge 内で muted に丸める。
"""

from django.template import Library
from django.utils.html import format_html
from django.utils.safestring import mark_safe


register = Library()


# app.css に定義済みのバッジ variant。これ以外は使わない（_render_badge で muted に丸める）。
_ALLOWED_VARIANTS = {"success", "warning", "error", "muted", "info"}

# 安全側の既定 variant。
_FALLBACK_VARIANT = "muted"


# ----------------------------------------------------------------------
# 色マップ：(モデル名, フィールド名) → {内部値: variant}
#   フィールド文脈込みで引くため、同じ "active" でもモデルごとに別 dict に属する。
#   ここに無い (モデル, フィールド) / 値は _FALLBACK_VARIANT（muted）に倒す。
# ----------------------------------------------------------------------
_STATUS_BADGE_VARIANTS = {
    ("Contact", "status"): {
        "primary": "success",   # 主コンタクト
        "active": "warning",    # 副コンタクト
        "inactive": "muted",    # 旧コンタクト
    },
    ("Person", "status"): {
        "active": "success",    # 通常
        "merged": "info",       # マージ済み
        "archived": "muted",    # アーカイブ
    },
    ("Campaign", "status"): {
        "draft": "muted",       # 下書き
        "scheduled": "info",    # 予約配信待機中
        "sending": "warning",   # 配信中（in-flight・二重送信注意）
        "done": "success",      # 配信完了
        "failed": "error",      # 配信失敗
    },
}


# バッジ表示専用のラベル短縮（HIG 対照表は別途是正。バッジは幅が狭いセル＝状態列に
# 収めるため短縮形を使う。get_FIELD_display（フォームの絞り込み選択肢・ドロップダウン等）
# には波及させず、本タグの描画ラベルだけ差し替える）。
#   - Contact.status の primary/active/inactive を「主」「副」「旧」へ短縮。
#   - Person.status / Campaign.status 等は対象外（get_FIELD_display のまま）。
_STATUS_BADGE_LABEL_OVERRIDES = {
    ("Contact", "status"): {
        "primary": "主",
        "active": "副",
        "inactive": "旧",
    },
}


def _render_badge(variant, label):
    """app-status-badge の span を組み立てる内部ヘルパー。

    [性質] 純関数（DB 操作なし・副作用なし）
    """
    if variant not in _ALLOWED_VARIANTS:
        variant = _FALLBACK_VARIANT
    return format_html(
        '<span class="app-status-badge app-status-badge--{}">{}</span>',
        variant,
        label,
    )


# ----------------------------------------------------------------------
# {% status_badge %} : choices フィールド用のステータスバッジ
# ----------------------------------------------------------------------


@register.simple_tag
def status_badge(instance, field="status"):
    """choices フィールドのステータスバッジ HTML を返す（HIG 原則4・3.8）。

    [性質] 純関数（DB 操作なし・副作用なし。get_FIELD_display は in-memory の choices を引く）
    [入力] instance: choices 付き CharField を持つモデルインスタンス
                     （Contact / Person / Campaign 等）
           field: str（対象フィールド名。既定 "status"）
    [出力] SafeString
        - 表示テキストは get_FIELD_display()（モデルラベル）から引く。テンプレに文言を直書きしない。
        - 色（variant）は (モデル名, フィールド名) 文脈の色マップから引く。
        - instance が None → 空文字。
        - 色マップ未登録の値 / フィールド → variant=muted、テキストは get_FIELD_display()
          （安全側 fallback。生の英字値は出さない）。
    """
    if instance is None:
        return mark_safe("")

    value = getattr(instance, field, None)

    # 表示テキストは必ずモデルラベル（get_FIELD_display）から。無ければ最終手段で値。
    display_getter = getattr(instance, f"get_{field}_display", None)
    label = display_getter() if callable(display_getter) else value

    # バッジ専用のラベル短縮（Contact.status の primary/active のみ）。未登録は据え置き。
    label_override = _STATUS_BADGE_LABEL_OVERRIDES.get(
        (type(instance).__name__, field), {}
    )
    label = label_override.get(value, label)

    variant_map = _STATUS_BADGE_VARIANTS.get((type(instance).__name__, field), {})
    variant = variant_map.get(value, _FALLBACK_VARIANT)

    return _render_badge(variant, label)


# ----------------------------------------------------------------------
# {% bool_badge %} : BooleanField 用のバッジ（ラベル・色は呼び出し側 kwargs）
# ----------------------------------------------------------------------


@register.simple_tag
def bool_badge(value, *, true_label, false_label,
               true_variant="success", false_variant="muted"):
    """BooleanField の値に応じたバッジ HTML を返す（HIG 原則4・3.8）。

    ラベル文字列と variant は呼び出し側（テンプレ）からキーワード引数で受け取る。
    FG2 で kwargs 形のタグ呼び出しはこれが初導入。今後 bool_badge は kwargs で統一する。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] value: bool（真偽値）
           true_label / false_label: str（True / False のときに出すラベル。必須 kwarg）
           true_variant / false_variant: str（それぞれの variant。既定 success / muted）
    [出力] SafeString
        - value が真 → true_label を true_variant で。
        - value が偽 → false_label を false_variant で。
        - variant が 5 種以外なら muted に丸める（_render_badge）。

    呼び出し例：
      {% bool_badge person.is_flagged true_label="要確認" false_label="確認済み"
                    true_variant="warning" false_variant="muted" %}
    """
    if value:
        return _render_badge(true_variant, true_label)
    return _render_badge(false_variant, false_label)
