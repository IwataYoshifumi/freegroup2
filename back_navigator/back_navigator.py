import json
import base64
import logging
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from django.conf import settings
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)


class BackNavigator:
    """
    Djangoの画面遷移において「戻るボタン」を実現するクラス。
    検索状態および遷移履歴をURLに保持し、正しい画面へ戻る仕組みを提供する。

    【重要】push_current は1リクエストにつき1回だけ呼ぶこと。
    複数回呼ぶと同じ画面が重複してスタックに積まれる。

    【重複チェック】
    view_name + view_kwargs が同じ場合はスタックしない。
    （同じ画面・同じIDへの戻りリダイレクト等で二重積みを防ぐ）
    """

    PARAM_NAME = "back_stack"
    _BASE_EXCLUDE_KEYS = {"csrfmiddlewaretoken", "next", "_"}
    MAX_STACK_DEPTH = 5

    @classmethod
    def get_exclude_keys(cls):
        """除外するクエリキーの集合を返す"""
        return cls._BASE_EXCLUDE_KEYS | {cls.PARAM_NAME}

    def __init__(self, request):
        self.request = request
        self._pushed = False  # 1リクエスト1回制御フラグ
        # GETを優先、なければPOSTからback_stackを復元
        raw = request.GET.get(self.PARAM_NAME) or request.POST.get(self.PARAM_NAME, "")
        self.back_stack = self._decode_stack(raw)
        self.current_url = request.path
        self.current_query = request.GET
        # push_current前の状態をキャッシュ（戻るボタン用）
        self._cache_pre_push()

    # -------------------------
    # キャッシュ処理
    # -------------------------

    def _cache_pre_push(self):
        """push_current前の状態をキャッシュする（戻るボタン用）"""
        self._back_exist = len(self.back_stack) >= 1
        self._back_all_exist = len(self.back_stack) >= 2
        self._back_url = self._calc_back_url()
        self._back_all_url = self._calc_back_all_url()
        self._back_title = self._calc_back_title()
        self._back_all_title = self._calc_back_all_title()

    def _cache_post_push(self):
        """push_current後の状態をキャッシュする（append_back_url・hidden_fields用）"""
        self._encoded_cache = self._calc_encode_stack()

    # -------------------------
    # 計算ロジック
    # -------------------------

    def _calc_encode_stack(self, stack=None):
        """back_stackをJSON→base64エンコードして返す（パディング=を除去）"""
        target = stack if stack is not None else self.back_stack
        json_str = json.dumps(target, ensure_ascii=False)
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode()
        return encoded.rstrip("=")

    def _calc_back_url(self):
        """直前の画面へ戻るURLを計算して返す"""
        if not self.back_stack:
            return ""

        stack = self.back_stack

        # トップが現在のパスと同じなら自分自身なので1つ飛ばす
        if stack[-1]["url"].split("?")[0] == self.request.path:
            stack = stack[:-1]

        if not stack:
            return ""

        prev_stack = stack[:-1]
        base_url = stack[-1]["url"]
        if not prev_stack:
            return base_url
        encoded = self._calc_encode_stack(prev_stack)
        params = urlencode({self.PARAM_NAME: encoded})
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{params}"

    def _calc_back_all_url(self):
        """最初の画面へ戻るURLを計算して返す"""
        if not self.back_stack:
            return ""
        return self.back_stack[0]["url"]

    def _calc_back_title(self):
        """直前の画面のタイトルを計算して返す"""
        if not self.back_stack:
            return None
        return self.back_stack[-1].get("title") or "戻る"

    def _calc_back_all_title(self):
        """最初の画面のタイトルを計算して返す"""
        if not self.back_stack:
            return None
        return self.back_stack[0].get("title") or "最初に戻る"

    def _normalize_kwargs(self, kwargs):
        """view_kwargsをstr型に正規化する（UUID・int混在に対応）"""
        return {k: str(v) for k, v in kwargs.items()}

    # -------------------------
    # エンコード・デコード
    # -------------------------

    def _encode_stack(self):
        """エンコード済みback_stackを返す（キャッシュがなければその場で生成）"""
        if not hasattr(self, "_encoded_cache"):
            self._encoded_cache = self._calc_encode_stack()
        return self._encoded_cache

    def _decode_stack(self, value):
        """base64→JSONデコードしてback_stackを復元する"""
        if not value:
            return []
        try:
            padded = value + "=" * (-len(value) % 4)
            json_str = base64.urlsafe_b64decode(padded.encode()).decode()
            data = json.loads(json_str)
            if not isinstance(data, list):
                return []
            for item in data:
                if not isinstance(item, dict):
                    return []
                if not isinstance(item.get("url"), str):
                    return []
                item.setdefault("title", "")
                item.setdefault("view_name", "")
                item.setdefault("view_kwargs", {})
            return data
        except Exception as e:
            logger.warning("BackNavigator._decode_stack: invalid back_stack: %s", e)
            return []

    # -------------------------
    # Viewで使うメソッド
    # -------------------------

    def push_current(self, title, keys):
        """
        現在画面の状態をback_stackに積む。
        1リクエストにつき1回だけ呼ぶこと。

        title: 画面名（戻るボタンのラベル表示用）
        keys:  保存したいクエリパラメータのキーリスト（空はエラー）

        【重複チェック】
        view_name + view_kwargs が同じ場合はスタックしない。
        """
        # keysが空の場合の処理
        if not keys:
            if settings.DEBUG:
                raise ValueError("BackNavigator.push_current: keys must not be empty")
            else:
                logger.warning("BackNavigator.push_current: keys must not be empty")
                return

        # 1リクエスト1回制御
        if self._pushed:
            if settings.DEBUG:
                raise RuntimeError(
                    "BackNavigator.push_current: already pushed in this request"
                )
            else:
                logger.warning(
                    "BackNavigator.push_current: already pushed in this request"
                )
                return

        # view_name と view_kwargs を取得
        resolver = self.request.resolver_match
        current_view_name = resolver.view_name
        current_view_kwargs = self._normalize_kwargs(resolver.kwargs)

        # 重複チェック：view_name + view_kwargs が同じならスタックしない
        if self.back_stack:
            top = self.back_stack[-1]
            if (
                top.get("view_name") == current_view_name
                and top.get("view_kwargs") == current_view_kwargs
            ):
                self._pushed = True
                self._cache_post_push()
                return

        # keysの順序を保持しつつ除外キーを弾く
        exclude = self.get_exclude_keys()
        safe_keys = [k for k in keys if k not in exclude]
        # getlist で複数値（チェックボックス等）に対応
        query = []
        for k in safe_keys:
            for v in self.request.GET.getlist(k):
                query.append((k, v))
        url = self.request.path
        if query:
            url += "?" + urlencode(query, doseq=True)

        self.back_stack.append(
            {
                "title": title,
                "url": url,
                "view_name": current_view_name,
                "view_kwargs": current_view_kwargs,
            }
        )

        # 最大深度を超えたらindex=1（2番目）を捨てる（index=0は保持）
        if len(self.back_stack) > self.MAX_STACK_DEPTH:
            self.back_stack.pop(1)

        self._pushed = True
        # push後の状態をキャッシュ（append_back_url・hidden_fields用）
        self._cache_post_push()

    # -------------------------
    # テンプレートで使うメソッド（カスタムタグ経由）
    # -------------------------

    def append_url(self, url):
        """URLにback_stackをエンコードして付与して返す（既存back_stackは除去）"""
        if not self.back_stack:
            return url

        # 既存のback_stackを除去してからappend
        parsed = urlparse(url)
        params = {
            k: v[0] for k, v in parse_qs(parsed.query).items() if k != self.PARAM_NAME
        }
        clean_query = urlencode(params)
        clean_url = urlunparse(parsed._replace(query=clean_query))

        new_param = urlencode({self.PARAM_NAME: self._encode_stack()})
        separator = "&" if clean_query else "?"
        return f"{clean_url}{separator}{new_param}"

    def hidden_fields(self):
        """POSTフォーム用のhiddenフィールドを生成"""
        if not self.back_stack:
            return ""
        return mark_safe(
            f'<input type="hidden" name="{self.PARAM_NAME}" value="{self._encode_stack()}">'
        )

    # -------------------------
    # プロパティ（キャッシュした値を返す）
    # -------------------------

    @property
    def back_exist(self):
        """戻り先が存在するか"""
        return self._back_exist

    @property
    def back_all_exist(self):
        """最初に戻るボタンを表示するか（スタックが2個以上ある場合）"""
        return self._back_all_exist

    @property
    def back_url(self):
        """直前の画面へ戻るURL"""
        return self._back_url

    @property
    def back_all_url(self):
        """最初の画面へ戻るURL"""
        return self._back_all_url

    @property
    def back_title(self):
        """直前の画面のタイトル（表示用）"""
        return self._back_title

    @property
    def back_all_title(self):
        """最初の画面のタイトル（表示用）"""
        return self._back_all_title
