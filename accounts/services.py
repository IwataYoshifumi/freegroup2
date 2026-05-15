from django.db import transaction


def apply_role(user, role):
    """Role を付与し、対応する PermissionGroup を自動設定する（仕様書 §8）。

    [性質] 副作用あり（DB 書込：user.role 更新 + user.groups M2M set/clear、transaction.atomic 内）
    [入力] user: CustomUser インスタンス
           role: Role インスタンス または None
    [出力] None

    ⚠️ この関数は Role が変わったときにだけ呼ぶこと。
    Role が変わっていない単なる保存で呼ぶと、手動で追加した Group がサイレントに消える。
    呼び出し側でガードする（CustomUserAdmin.save_model 参照）。

    ⚠️ user.save() を内部で呼ぶため、CustomUser の post_save シグナルでさらに
    apply_role() を呼ぶような実装は禁止（無限ループ回避）。

    ⚠️ Migration からは呼べない（historical model 制約）。
    Migration では本関数を import せず、apps.get_model() で取得した historical model に
    対して user.groups.set() を直接呼ぶこと（仕様書 §8 末尾「Migration からの呼び出し」）。
    """
    with transaction.atomic():
        user.role = role
        user.save(update_fields=["role"])
        if role is None:
            user.groups.clear()
        else:
            user.groups.set(role.default_groups.all())
