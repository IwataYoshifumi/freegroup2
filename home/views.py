from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from accounts.constants import PersonLinkStatus
from contacts.models import Contact
from persons.models import Person


class HomeView(LoginRequiredMixin, TemplateView):
    """ホーム画面（仕様書 §12.4）。

    未紐付け User の email と一致する Contact があれば、ホーム画面に紐付け候補
    アラートを表示する。ORM 完結クエリ（person__user__isnull=True で OneToOne
    逆参照を直接フィルタ、Python 側ループ排除、N+1 回避）。
    """

    template_name = "home/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        if user.person is None and user.email:
            candidates_qs = Contact.objects.filter(
                email=user.email,
                person__status=Person.Status.ACTIVE,
                person__user__isnull=True,
            ).select_related("person")
            candidates = list(candidates_qs)

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
