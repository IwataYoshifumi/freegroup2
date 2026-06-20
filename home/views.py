from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from accounts.services import self_link_alert_context
from back_navigator.back_navigator import BackNavigator


class HomeView(LoginRequiredMixin, TemplateView):
    """ホーム画面（仕様書 §12.4）。

    未紐付け User の email と一致する Contact があれば、ホーム画面に紐付け候補
    アラートを表示する。照合基準は確認画面ガードと共通の self_link_candidate_contacts
    に集約（入口・出口で基準を揃え、primary 以外の Contact 一致による「アラートは出るが
    確認画面で弾かれる」詰みを解消）。
    """

    template_name = "home/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        # 確認画面リンクに back_stack を引き継げるよう BackNavigator を渡す（push_current は呼ばない＝
        # ホームはルート画面でクエリ状態を持たないため push しない。dummy keys を避ける）。
        ctx["back"] = BackNavigator(self.request)

        if user.person is None:
            # 状態算出は accounts.services の共通ヘルパーに一本化（profile と同一基準）。
            # 0 件（no_candidate）も含め常に context に載せる。ようこそカード下部に本人向けの
            # 名刺取り込み促しカードを出すため（上部 include は show_no_candidate 未指定のままなので
            # 0 件アラートを描画せず、single/multiple の既存表示挙動は不変）。
            ctx.update(self_link_alert_context(user))

        return ctx
