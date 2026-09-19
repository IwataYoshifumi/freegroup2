"""Contact の所有者ベース編集権限判定（Phase 7 段2-B）。

mailings.services.permissions（can_view_campaign / visible_campaigns_for）の構造に倣う。
所有者判定が必要なのは AJAX 個別フィールド修正・確認（§10.6.4 ケース 4）の 2 エンドポイントで、
他人の Contact を勝手に編集・確認できる穴を塞ぐためのゲートをここに一元化する
（View 層は判定ロジックを二重に持たず、この関数を正本として呼ぶ）。
"""


def can_edit_contact(user, contact):
    """単一 Contact の編集可否判定の正本（仕様書 v1.6 §5.3 / Phase 7 段2-B）。"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.has_perm("contacts.edit_all_contacts"):
        return True
    if contact.managed_by_id == user.id or contact.created_by_id == user.id:
        return True
    from permissions.services import AccessListService
    return AccessListService.can_edit_contact(user, contact)
