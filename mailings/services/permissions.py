"""Campaign の所有者ベース権限判定（rev20 §14.4.1 / §14.5.2）。

所有者判定が必要なのは Campaign 1 モデルのみ。EmailTemplate / SuppressedEmail /
MailingList には所有者判定を設けない（rev20 §14.4.2 / §14.4.3。テンプレートは紐付く
Campaign の判定に従属）。

判定が示す所有者集合（view_all_campaigns 持ち or 作成者本人）は、閲覧・編集・予約登録・
テスト送信・CSV ダウンロードのすべてで同一。⑤（View 層）は全アクションでこの関数群を
正本として呼ぶ前提（View 層 + Service 層の二重防衛、rev20 §14.5 / v1.5.0 §13.7）。
"""


def can_view_campaign(user, campaign):
    """単一 Campaign の所有者詳細判定の正本（rev20 §14.5.2）。

    [性質] 純関数（DB 操作なし。user.has_perm と campaign の属性参照のみ）
    [入力] user: CustomUser、campaign: Campaign
    [出力] bool（閲覧可なら True）

    mailings.view_all_campaigns を持つ（横断権限：admin / 営業マネージャ等）か、
    または作成者本人なら True。Campaign.has_view_permission はこの関数の薄いラッパ。
    """
    if user.has_perm("mailings.view_all_campaigns"):
        return True
    return campaign.created_by_id == user.id


def visible_campaigns_for(user, queryset):
    """リスト用：user が閲覧できる Campaign に QuerySet レベルで絞る（rev20 §14.4.1）。

    [性質] 準関数（QuerySet を構築するだけで即時の DB I/O はなし。評価時に読み取り）
    [入力] user: CustomUser、queryset: QuerySet[Campaign]（呼び出し側の基底 QuerySet）
    [出力] QuerySet[Campaign]

    mailings.view_all_campaigns 持ちなら基底 QuerySet をそのまま返し、でなければ
    created_by=user で絞る。QuerySet レベルで絞ることで、一覧表示で 1 件ずつ
    can_view_campaign を呼ぶ N+1 を避ける（v1.5.0 §7.2）。
    can_view_campaign と同一の所有者集合（view_all 持ち or 本人）を返す。
    """
    if user.has_perm("mailings.view_all_campaigns"):
        return queryset
    return queryset.filter(created_by=user)
