"""Contact の所有者ベース編集権限判定（Phase 7 段2-B）。

mailings.services.permissions（can_view_campaign / visible_campaigns_for）の構造に倣う。
所有者判定が必要なのは AJAX 個別フィールド修正・確認（§10.6.4 ケース 4）の 2 エンドポイントで、
他人の Contact を勝手に編集・確認できる穴を塞ぐためのゲートをここに一元化する
（View 層は判定ロジックを二重に持たず、この関数を正本として呼ぶ）。
"""


def can_edit_contact(user, contact):
    """単一 Contact の編集可否判定の正本（Phase 7 段2-B）。

    [性質] 純関数（DB 操作なし。user.has_perm と contact の属性参照のみ）
    [入力] user: CustomUser、contact: Contact
    [出力] bool（編集可なら True）

    contacts.edit_all_contacts を持つ（横断権限：admin / 営業マネージャ等）か、
    作成者本人（created_by）または管理者本人（managed_by）なら True。
    can_view_campaign（mailings）と同じく has_perm 経由で判定するため、
    スーパーユーザーは横断権限を自動的に満たす。
    """
    if user.has_perm("contacts.edit_all_contacts"):
        return True
    return contact.created_by_id == user.id or contact.managed_by_id == user.id
