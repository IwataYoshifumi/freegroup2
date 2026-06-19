from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from accounts.constants import PersonLinkStatus
from accounts.services import self_link_candidate_contacts
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
            # 入口・出口で同一基準（accounts.services の共通定義）を参照する。
            candidates = list(self_link_candidate_contacts(user))

            distinct_persons = {c.person_id for c in candidates}
            if len(distinct_persons) > 1:
                ctx["person_link_status"] = (
                    PersonLinkStatus.MULTIPLE_CANDIDATES_NEED_MERGE
                )
                ctx["person_link_candidates"] = candidates
            elif distinct_persons:
                ctx["person_link_status"] = PersonLinkStatus.SINGLE_CANDIDATE
                ctx["person_link_candidates"] = candidates

        return ctx
