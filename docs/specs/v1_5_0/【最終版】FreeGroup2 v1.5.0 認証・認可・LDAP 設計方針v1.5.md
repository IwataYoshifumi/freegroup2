# FreeGroup2 v1.5.0 認証・認可・LDAP 設計方針メモ

| 項目 | 内容 |
| --- | --- |
| バージョン | **v1.5** |
| 作成日 | 2026-05-12 |
| 改訂日 | 2026-05-15 |
| 作成者 | クロード君（コード君） |
| 対象バージョン | FreeGroup2 v1.5.0 |
| ステータス | **コード君実装着手可能版（Phase 0 完了反映、3点整合性回復）** |
| 前版 | v0.1 / v0.2 / v0.9 / v1.0 / v1.1 / v1.2 / v1.3 / v1.4 |

## 改訂履歴

### v1.5（2026-05-15）

Phase 0 完了後・Phase 1 着手前のレビューで判明した 3 点の整合性問題を解消。Web 版コード君からの修正依頼に基づきオーパス君が反映。

主な変更点（3 件）:

| # | 変更箇所 | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | §13.8 Person.Meta.permissions | `('link_user', ...)` の 2 行重複を削除（タイポ修正） | バグ修正 |
| 2 | §13.8 / 付録 B / 付録 C | `cards.*` Permission（`create_card` / `edit_card` / `merge_card`）と `card_*` Group（`card_admin` / `card_editor` / `card_viewer`）の定義・初期データ Migration・チェックリストを追記。§7.4・§8 で v1.5.0 実装と宣言されていた整合性を回復 | 整合性回復 |
| 3 | §12.7 accounts:urls | `user_list` / `user_detail` の URL ルート定義を追記（§12.6 RetireUserView から参照されているため） | 整合性回復 |

仕様書本体の他章（§1〜§12 / §13.1-13.7 / §14 / 付録 A / 付録 D）に変更なし。

### v1.4（2026-05-14）

**付録 D 論点 A（memberOf 受け皿の要否）を「実装する」で確定**。たんたんとの議論で「自社運用では AD 不要だが、顧客に AD 運用が多いため AD 対応は製品として必要」と確定。`AUTH_BACKEND` 環境変数で ON/OFF 制御可能なため、自社運用にも邪魔にならない。

主な変更点（付録 D の更新のみ、仕様書本体に変更なし）:

| # | 変更箇所 | 内容 |
| --- | --- | --- |
| 1 | 付録 D.3 論点 A | **「未決」→「確定: 実装する」** に格上げ。確定理由を明記 |
| 2 | 付録 D.4 | 「保険として実装する」→「確定方針として実装する」に表現を強化 |
| 3 | 付録 D.7 | 論点 A 削除版（将来別途作成）の余地は維持しつつ、現実装方針が確定であることを明示 |

#### 論点 A 確定の経緯（要約）

- たんたん発言: 「自社はAD運用は全然してないんだけど、お客さんがやっぱADが多いから、ADは対応しとかないかんかなっていうとこだよね」
- 製品要件として AD 対応が必要 → `LdapGroup` モデル + `sync_ldap_groups` 関数を実装
- 自社運用時は `AUTH_BACKEND=local` で同期コードが呼ばれないため、自社運用の邪魔にならない
- v1.6+ AccessList で AD 由来グループを使うかは引き続き未決（論点 F / G）

### v1.3（2026-05-14）

**未決論点メモを追記**。v1.2 で確定した `LdapGroup` リネームの妥当性は維持しつつ、`LdapGroup` の存在意義（memberOf 受け皿そのものが FreeGroup2 に必要か）と v1.6+ での `LdapGroup` × `UserGroup` の関係性について、たんたんとの議論で結論保留となった論点を末尾「付録 D 未決論点メモ」として記録。

主な変更点（仕様書本体に変更なし、付録追加のみ）:

| # | 変更箇所 | 内容 |
| --- | --- | --- |
| 1 | 付録 D（新設） | 未決論点メモ（memberOf 受け皿の要否、AD 依存型 vs 独立型、v1.6+ で LdapGroup と UserGroup の関係 など） |

#### v1.2 で確定した方針（再掲）

| バージョン | LdapGroup | UserGroup |
| --- | --- | --- |
| **v1.5.0** | LDAP `memberOf` 受け皿として実装 | 未実装（名前温存） |
| **v1.6+** | そのまま継続 | AccessList 用、FreeGroup2 上でカスタム作成可能 |

v1.6+ AccessList 設計では、LdapGroup と UserGroup の関係（継承 / 包含 / 独立）と AccessList のターゲット型は **v1.6+ 設計時に詰める**。v1.5.0 仕様書では触れない。

### v1.2（2026-05-14）

**命名の整理**: 旧 `UserGroup` を `LdapGroup` にリネーム。「UserGroup」の名前は v1.6+ AccessList 用に温存。

主な変更点（機械的なリネームのみ、設計内容に変更なし）:

| # | 変更箇所 | 内容 |
| --- | --- | --- |
| 1 | 全体 | モデル名 `UserGroup` → `LdapGroup`（LDAP `memberOf` 受け皿であることを明示） |
| 2 | §4.1 CustomUser | フィールド名 `user_groups_custom` → `ldap_groups` |
| 3 | §5.4 LDAP 同期 | 関数名 `sync_ldap_user_groups` → `sync_ldap_groups` |
| 4 | §1.2 含まないもの | v1.6+ で `UserGroup`（業務管理者が作る横断グループ）を新規追加と明記 |
| 5 | 付録 A | 「横断グループ」の対応を整理（LdapGroup=LDAP 由来、UserGroup=v1.6+ 業務用） |

#### 命名整理の意図

| 名前 | 用途 | バージョン |
| --- | --- | --- |
| **`LdapGroup`** | LDAP `memberOf` 属性の受け皿（自動同期） | v1.5.0 |
| **`UserGroup`** | 業務管理者が手動で作る横断グループ（AccessList 用） | v1.6+ |

これにより:
- LDAP 由来か業務由来かがモデル名で自明になる
- v1.6+ で AccessList のエントリ型として「ユーザ／部署／UserGroup」の 3 種を持てる（PHP 版 `Group` 相当）
- LdapGroup と UserGroup を AccessList で両方扱うか、片方だけかは v1.6+ で議論

### v1.1（2026-05-14）

v1.0 のコード君視点レビューで指摘された「実装で迷う箇所」を解消。**実装場所・UI 実装方針・初期データ Migration の具体例**を追加。

主な変更点（6 件 + 軽微 1 件）:

| # | 変更箇所 | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | §5.4 | **LDAP 同期処理の実装場所とトリガー**を追加（`accounts/ldap_sync.py`, `accounts/signals.py`、`django-auth-ldap` の `populate_user` シグナル経由） | ★実装場所 |
| 2 | §12.4 | **ホーム画面アラートの UI 実装方針**追加（テンプレート構造、URL、遷移、`PersonLinkStatus` 定数化） | ★UI 設計 |
| 3 | §12.7 | **紐付け／解除画面の View 実装サンプル**追加（URL、`LinkUserPersonView` / `UnlinkUserPersonView`、権限チェック） | ★View 設計 |
| 4 | §13.6 末尾 / §13.7 新設 | **View 層 vs Service 層の権限チェック責務分担表**を明示 | ★責務分担 |
| 5 | §13.8 新設 | **初期データ Migration の具体例**（命名規約表 + Group / Role / Permission 作成 Migration コード） | ★Migration |
| 6 | §8 末尾 | **`apply_role()` を Migration から呼ぶ方法**（historical model 制約と回避策） | ★Migration |
| 7 | §4.1 / §12.6 | **退職処理 UI**: Admin actions + 専用 View の両方を実装する方針を明示 | ★UI 設計 |

### v1.0（2026-05-14）

v0.9 のレビュー結果を反映した実装着手可能版。v1.4.2 実装の最新コミット（D-4d-1 第 3 弾改訂含む）と完全整合。13 項目改訂。

### v0.9（2026-05-14）

v1.4.2 実装コードを確認した上で全面書き直し。

### v0.2（2026-05-12）

レビュー結果を反映した修正版。

### v0.1（2026-05-12）

実装未確認の議論版。

---

## 0. このドキュメントの位置付け

FreeGroup2 v1.5.0 の認証・認可・LDAP 対応の設計方針を、たんたん（岩田好史）との議論結果としてまとめたもの。

**v1.1 の重要な前提**: v1.0 では「v1.4.2 実装と完全整合」を達成したが、コード君視点では「実装場所・UI・Migration の具体例」が不足していた。v1.1 はこれらを補い、**コード君が手元で迷わずに実装着手できる品質**を目指している。

姉妹資料:

- `freegroup_accesslist_調査結果.md` — FreeGroup PHP/Laravel 版アクセスリスト機構の調査
- `freegroup2_v1.4.2_仕様書.md`（仮称） — v1.4.2 の完成版仕様書（本資料はこの仕様書を補完するもので、競合する箇所は v1.4.2 仕様書が優先）

---

## 1. v1.5.0 のスコープ

### 1.1 含むもの

| 項目 | 内容 |
| --- | --- |
| **CustomUser モデル** | `AbstractUser` 継承。`settings.AUTH_USER_MODEL = 'accounts.CustomUser'`。**v1.4.2 開発中に先行して切替実施**（v1.5.0 では既存前提） |
| **認証バックエンド切替** | `AUTH_BACKEND` 環境変数で `local` / `ldap` / `both` を切替 |
| **LDAP 連携** | `django-auth-ldap` 使用、`.env` で接続情報管理、LDAP キャッシュ ON/OFF |
| **認可（機能レベル権限）** | Permission + PermissionGroup（Django 標準）+ カスタム Role |
| **組織データ土台** | Department（木構造、LDAP 同期対応）、LdapGroup（テーブル + LDAP `memberOf` 受け皿） |
| **既存 Person / Contact に `managed_by` 追加** | マイグレーション、退職時引き継ぎ用 |
| **CustomUser.person 追加** | OneToOneField で Person との 1:1 紐付け |
| **既存マージサービスへの権限拡張** | `Execute_Merge_Only` / `Execute_Merge_Undo` / `Mark_as_Different_Person` への権限チェック差分挿入 |
| **DuplicateCandidate 生成への両方紐付きガード** | `excluded_persons` 経由 |
| **ホーム画面アラート** | User-Person 自主紐付け促進 |
| **紐付け／解除サービス** | `link_user_to_person()` / `unlink_user_from_person()` |
| **退職処理** | `retire_user(user, successor)` サービス + **Admin actions + 専用 View 両方の UI** |

### 1.2 含まないもの（v1.6.0 以降）

| 項目 | 理由 |
| --- | --- |
| アクセスリスト（データレベル権限） | 名刺マージ機能で当面は不要 |
| BusinessCardList（コンテナ概念） | AccessList と同時に検討 |
| LdapGroup の UI / Admin 編集 | テーブル + LDAP 同期書き込みは v1.5.0、UI は v1.6+ |
| **UserGroup（業務管理者用横断グループ）** | **v1.6+ で AccessList と同時に新規追加**（PHP 版 `Group` 相当） |
| 制限閲覧者（freeBusyReader）相当 | AccessList と同時 |
| `Department.descendants()` の本格利用 | メソッドは用意するが、本番投入前に必ず django-mptt / treebeard / CTE に置換 |
| 「私はこの候補と別人です」アラート却下ボタン | UX 評価後に v1.5.x で判断 |
| バッチ LDAP 同期コマンド | 認証時の単発同期のみ実装 |

### 1.3 v1.5.0 で**触らない**もの（v1.4.2 の堅牢な実装を尊重）

| 領域 | v1.4.2 実装 |
| --- | --- |
| Person モデル定義 | `persons.models.Person`（UUID PK、`status` enum、`merged_into` FK 等） |
| Contact モデル定義 | `contacts.models.Contact`（UUID PK、`previous_person` / `previous_status` 等） |
| ContactFieldConfidence | OCR confidence 管理 |
| DuplicateCandidate | 重複候補管理 |
| PersonMergeLog | マージ履歴（status: undoable/undone/locked、1 階層復元の核） |
| ActionLog | 汎用イベントログ（自由文字列 action、GenericForeignKey） |
| `Execute_Merge_Only` 本体 | マージ実行サービス（D-4d-1 第 3 弾改訂含む） |
| `Execute_Merge_Undo` 本体 | マージ復元サービス |
| `Mark_as_Different_Person` 本体 | 別人判定サービス |
| `recover_duplicate_candidates` | DC 再構成（スコア再計算禁止） |
| `find_duplicate_contacts` / `determine_score_and_rank` | 重複検出ロジック |
| `PersonMergeLog.lock_past_logs()` | 1 階層復元保証メカニズム |
| 各 UI（DuplicateCandidateGroup* / PersonMergeLog*） | v1.4.2 の View / Form / Template |

---

## 2. 概念モデル（5 概念 + Role）

```
[認可の軸]
  Permission         ─ 個別の能力フラグ                            ← Django 標準 auth.Permission
  PermissionGroup    ─ Permission の束                            ← Django 標準 auth.Group + GroupProfile

[組織・ユーザの軸]
  Department         ─ 組織階層（部署ツリー）                     ← 独自モデル（parent FK）
  LdapGroup          ─ 横断グループ                                ← 独自モデル

[ユーザ表現の軸]
  CustomUser         ─ ユーザ本体                                  ← AbstractUser 継承
  Role               ─ 業務上の肩書き                              ← 独自モデル
```

### 2.1 PHP 版との対応

| FreeGroup2 | PHP 版 FreeGroup | 備考 |
| --- | --- | --- |
| Permission | `RoleList` | 命名修正 |
| PermissionGroup（`auth.Group`） | `RoleGroup` | Django 標準で済む |
| Department | `Dept` | 木構造化 |
| LdapGroup（v1.5.0） | （該当なし） | LDAP `memberOf` の受け皿（新規概念） |
| UserGroup（v1.6+ で新規） | `Group`（横断グループ） | 業務管理者用、AccessList 用に温存 |
| CustomUser | `User` | フィールド追加 |
| Role | （該当なし） | 業務ロールを明示化 |

---

## 3. モデル別カスタム化方針

| モデル | 採用方針 | 理由 |
| --- | --- | --- |
| **CustomUser** | `AbstractUser` を継承（`AUTH_USER_MODEL`） | Django が公式に差し替えサポート |
| **Permission** | **Django 標準のまま** | 拡張要件なし |
| **Group** | **標準 + プロファイル並走** | Django は `auth.Group` の置換をサポートしない |
| **Role** | **独自モデル** | 業務ロールは Django 標準に無い概念 |
| **Department** | **独自モデル** | 階層構造、LDAP 同期、auth_source の管理 |
| **LdapGroup** | **独自モデル** | LDAP `memberOf` の受け皿（v1.5.0 新規） |

---

## 4. モデル定義（v1.5.0 新規）

### 4.1 CustomUser

```python
# apps/accounts/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models


class AuthSource(models.TextChoices):
    LOCAL = 'local', 'ローカル'
    LDAP  = 'ldap',  'LDAP'


class CustomUser(AbstractUser):
    role               = models.ForeignKey('Role', null=True, blank=True,
                                            on_delete=models.SET_NULL,
                                            related_name='users')
    person             = models.OneToOneField('persons.Person', null=True, blank=True,
                                                on_delete=models.SET_NULL,
                                                related_name='user',
                                                help_text='ログイン中のユーザに対応する Person（任意）。'
                                                          '1 User : 0..1 Person、Person 側も 1 User までに制限。'
                                                          '紐付け運用の詳細は §12 を参照。')
    auth_source        = models.CharField(max_length=16,
                                           choices=AuthSource.choices,
                                           default=AuthSource.LOCAL)
    department         = models.ForeignKey('Department', null=True, blank=True,
                                            on_delete=models.SET_NULL,
                                            related_name='users')
    ldap_groups = models.ManyToManyField('LdapGroup', blank=True,
                                                 related_name='members',
                                                 help_text='横断グループ（v1.5.0 では空運用も可）')
    ldap_dn            = models.CharField(max_length=512, null=True, blank=True, unique=True,
                                            help_text='LDAP 同期時の突合キー（LDAP 由来ユーザのみ）')
```

#### ⚠️ 重要: AUTH_USER_MODEL 切替は v1.4.2 開発中に先行実施する

**Django 公式ドキュメントの警告**:
> Changing AUTH_USER_MODEL after you've created database tables is significantly more difficult since it affects foreign keys and many-to-many relationships.

**本プロジェクトの方針**（v1.0 で確定）:

- **v1.4.2 開発中（DB リセット可能なうち）に CustomUser 切替を済ませる**
- v1.5.0 着手時点では `settings.AUTH_USER_MODEL = 'accounts.CustomUser'` が既に有効

**v1.4.2 段階で実施すること**（v1.5.0 着手前の前提条件）:

1. `accounts/models.py` に CustomUser を最小定義
2. `settings.AUTH_USER_MODEL = 'accounts.CustomUser'` 設定
3. DB リセット + `python manage.py migrate` で再構築
4. 既存テストが全件 PASS することを確認

#### CustomUserAdmin（退職処理 Admin actions 含む）

```python
# apps/accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.shortcuts import render
from .models import CustomUser
from .services import apply_role, retire_user


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display  = ('username', 'email', 'role', 'department',
                     'auth_source', 'is_active', 'last_login')
    list_filter   = ('auth_source', 'role', 'department', 'is_active', 'is_staff')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    list_select_related = ('role', 'department')
    actions = ['retire_user_action']

    def get_fieldsets(self, request, obj=None):
        """user_permissions を fieldsets から動的に除外。"""
        fieldsets = super().get_fieldsets(request, obj)
        cleaned = []
        for name, opts in fieldsets:
            if 'fields' in opts and 'user_permissions' in opts['fields']:
                new_fields = tuple(f for f in opts['fields'] if f != 'user_permissions')
                cleaned.append((name, {**opts, 'fields': new_fields}))
            else:
                cleaned.append((name, opts))
        return tuple(cleaned)

    def get_form(self, request, obj=None, **kwargs):
        """user_permissions を form base_fields からも除外（URL 直叩き保護）。"""
        form = super().get_form(request, obj, **kwargs)
        form.base_fields.pop('user_permissions', None)
        return form

    def save_model(self, request, obj, form, change):
        """Role が変わった時のみ apply_role() を呼ぶガード。"""
        old_role_id = None
        if change:
            old_role_id = self.model.objects.filter(pk=obj.pk).values_list('role_id', flat=True).first()
        super().save_model(request, obj, form, change)
        if not change or old_role_id != obj.role_id:
            apply_role(obj, obj.role)

    def retire_user_action(self, request, queryset):
        """選択されたユーザを退職処理する Admin アクション（インターメディエイト画面付き）。

        詳細仕様は §12.6 参照。
        """
        if 'apply' in request.POST:
            # 確定ボタンが押された → 退職処理実行
            successor_id = request.POST.get('successor')
            successor = CustomUser.objects.get(pk=successor_id)
            count = 0
            for user in queryset:
                try:
                    retire_user(user=user, successor=successor)
                    count += 1
                except Exception as e:
                    self.message_user(request, f'{user.username} の退職処理失敗: {e}', level='error')
            self.message_user(request, f'{count}名の退職処理が完了しました')
            return None

        # 初回呼び出し → 後継者選択のインターメディエイト画面を表示
        successor_choices = CustomUser.objects.filter(is_active=True).exclude(
            pk__in=queryset.values_list('pk', flat=True)
        )
        return render(request, 'admin/accounts/retire_user_intermediate.html', {
            'users': queryset,
            'successor_choices': successor_choices,
            'action': 'retire_user_action',
            'select_across': request.POST.get('select_across', '0'),
        })
    retire_user_action.short_description = '退職処理（後継者選択あり）'
```

### 4.2 Role

```python
class Role(models.Model):
    name           = models.CharField(max_length=100, unique=True,
                                       help_text='画面表示名（例: 管理者, 営業, 閲覧者）')
    code           = models.CharField(max_length=32, unique=True,
                                       help_text='プログラム判定用の安定キー（admin, sales, viewer）。'
                                                 '作成後の変更不可。変更したい場合はマイグレーション必須')
    memo           = models.TextField(blank=True)
    sort_order     = models.IntegerField(default=0)
    default_groups = models.ManyToManyField('auth.Group', blank=True,
                                              related_name='default_for_roles',
                                              help_text='このRoleを付与した際に自動で入れるGroup')
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name
```

### 4.3 GroupProfile（auth.Group の並走拡張）

```python
from django.contrib.auth.models import Group
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import AuthSource


class GroupProfile(models.Model):
    group       = models.OneToOneField(Group, on_delete=models.CASCADE,
                                        related_name='profile', primary_key=True)
    memo        = models.TextField(blank=True)
    is_default  = models.BooleanField(default=False,
                                       help_text='v1.5.0 では未使用。v1.6+ で運用方針確定')
    auth_source = models.CharField(max_length=16, choices=AuthSource.choices, default=AuthSource.LOCAL)
    ldap_dn     = models.CharField(max_length=512, null=True, blank=True, unique=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=Group)
def ensure_group_profile(sender, instance, created, **kwargs):
    if created:
        GroupProfile.objects.get_or_create(group=instance)
```

Admin 編集ガード（LDAP 由来は読み取り専用）は v1.0 §4.3 と同じ実装。

### 4.4 Department

```python
class Department(models.Model):
    code        = models.CharField(max_length=64, unique=True, null=True, blank=True)
    name        = models.CharField(max_length=200)
    parent      = models.ForeignKey('self', null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='children')
    auth_source = models.CharField(max_length=16, choices=AuthSource.choices, default=AuthSource.LOCAL)
    ldap_dn     = models.CharField(max_length=512, null=True, blank=True, unique=True)
    is_active   = models.BooleanField(default=True)
    sort_order  = models.IntegerField(default=0)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['parent']),
            models.Index(fields=['auth_source']),
        ]
        ordering = ['sort_order', 'name']

    def descendants(self, include_self=False, _visited=None):
        """配下の部署を再帰取得（循環参照対策付き）。

        ⚠️ N+1 問題あり。v1.6+ で AccessList から本格利用する前に
        必ず django-mptt / treebeard / Recursive CTE に置換すること。

        【v1.5.0 での使用可否】
        - ✅ 使ってよい: テストコード、Admin 画面（部署 10 件未満前提）
        - ❌ 使ってはいけない: LDAP 同期内、認証フロー、cron 処理
        - 判断基準: 本番運用で 1 回の処理で 10 部署以上を辿る可能性がある場合は使わない
        """
        if _visited is None:
            _visited = set()
        if self.pk in _visited:
            return []
        _visited.add(self.pk)
        result = [self] if include_self else []
        for child in self.children.all():
            result.extend(child.descendants(include_self=True, _visited=_visited))
        return result

    def clean(self):
        from django.core.exceptions import ValidationError
        parent = self.parent
        while parent:
            if parent.pk == self.pk:
                raise ValidationError('循環参照は禁止です')
            parent = parent.parent
```

### 4.5 LdapGroup

```python
class LdapGroup(models.Model):
    name        = models.CharField(max_length=200, unique=True)
    memo        = models.TextField(blank=True)
    auth_source = models.CharField(max_length=16, choices=AuthSource.choices, default=AuthSource.LOCAL)
    ldap_dn     = models.CharField(max_length=512, null=True, blank=True, unique=True)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
```

#### LDAP 由来 Group / Department / LdapGroup の削除運用

- LDAP 側で消えた Group は、次回同期で**自動削除はしない**（保守的運用）
- 管理者が Admin で手動削除する
- 将来的に LDAP 同期コマンド（v1.5.x）で `--prune-deleted` オプションを追加する余地あり
- `Department` / `LdapGroup` も同様（`is_active=False` での論理削除も可）

---

## 5. 認証バックエンドと LDAP 連携

### 5.1 `AUTH_BACKEND` 環境変数

`.env` で切替:

```
AUTH_BACKEND=local    # ローカル認証のみ
AUTH_BACKEND=ldap     # LDAP認証のみ（ローカル管理者は別途保持）
AUTH_BACKEND=both     # ローカル + LDAP の両方を試行
```

`settings.py` 展開:

```python
if AUTH_BACKEND == 'ldap':
    AUTHENTICATION_BACKENDS = [
        'django_auth_ldap.backend.LDAPBackend',
        'django.contrib.auth.backends.ModelBackend',  # 緊急時のローカル管理者用
    ]
elif AUTH_BACKEND == 'local':
    AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
elif AUTH_BACKEND == 'both':
    AUTHENTICATION_BACKENDS = [
        'django_auth_ldap.backend.LDAPBackend',
        'django.contrib.auth.backends.ModelBackend',
    ]
```

### 5.2 LDAP 接続情報とキャッシュ

`.env`:

```
LDAP_SERVER_URI=ldap://ldap.example.com:389
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=...
LDAP_USER_SEARCH_BASE=ou=People,dc=example,dc=com
LDAP_GROUP_SEARCH_BASE=ou=Groups,dc=example,dc=com
LDAP_CACHE_ENABLED=true
LDAP_CACHE_TTL=3600
```

`settings.py` でのキャッシュ設定:

```python
# settings.py
import os

# django-auth-ldap 標準のキャッシュ機構を使う
AUTH_LDAP_CACHE_TIMEOUT = (
    int(os.getenv('LDAP_CACHE_TTL', 3600))
    if os.getenv('LDAP_CACHE_ENABLED') == 'true'
    else 0  # 0 でキャッシュ無効
)
```

### 5.3 LDAP 属性マッピング

| LDAP/AD 属性 | Django モデルへの反映 |
| --- | --- |
| `distinguishedName` (DN) | `CustomUser.ldap_dn` |
| `sAMAccountName` (AD) / `uid` (LDAP) | `CustomUser.username` |
| `mail` | `CustomUser.email` |
| `cn`, `displayName` | `CustomUser.first_name + last_name` |
| `department` | Department を upsert し `CustomUser.department` に設定 |
| `departmentNumber` | `Department.code` |
| `ou`（複数値） | Department の階層復元（任意、v1.6+） |
| `memberOf` | LdapGroup を upsert し `CustomUser.ldap_groups` に設定 |
| `userAccountControl` (AD) / `nsAccountLock` (389DS) | `CustomUser.is_active`（**LDAP disabled のみ反映**、§5.4 参照） |
| `manager` | （v1.5.0 では未使用） |

### 5.4 LDAP 同期フローと実装場所（★ v1.1 で追加）
#### 全体フロー

```
LDAP認証成功
   │
   ├─ ユーザ属性取得
   │
   ├─ ローカル / LDAP の username 衝突チェック
   │    ├ 同じ username の auth_source='local' ユーザが既存
   │    │   → LDAP ユーザを作成せず、エラーログ + 管理者通知
   │    └ 衝突なし → 次へ
   │
   ├─ CustomUser upsert
   │    ├ ldap_dn で突合
   │    ├ 【新規作成時のみ】
   │    │   ├ auth_source = 'ldap'
   │    │   ├ is_active   = LDAP 側の有効状態（取れなければ True）
   │    │   ├ role        = None（管理者承認待ち）
   │    │   ├ groups      = []
   │    │   └ password    = set_unusable_password() ★ セキュリティ
   │    └ 【既存更新時】
   │        ├ email, first_name, last_name, department は LDAP から上書き
   │        ├ is_active:
   │        │   ├ LDAP 側で disabled → False に同期（即時反映）
   │        │   ├ LDAP 側で enabled  → 触らない（退職処理を尊重）
   │        │   └ LDAP 側が取得不可  → 触らない
   │        ├ role は触らない（管理者管理）
   │        └ groups は触らない（PermissionGroup は手動管理）
   │
   ├─ Department upsert  (auth_source='ldap')
   ├─ LdapGroup upsert (memberOf 経由)
   └─ groups (auth.Group) は触らない
```

#### 実装場所と関数構成（★ v1.1 で追加）

LDAP 認証の本体は `django-auth-ldap` の `LDAPBackend` が担う。v1.5.0 で追加する同期処理は以下のファイル構成で実装する:

```
apps/accounts/
  ├── ldap_sync.py       ← 同期処理本体（純粋な関数群、シグナルから呼ばれる）
  ├── signals.py         ← django-auth-ldap シグナルフック
  └── apps.py            ← ready() で signals を import
```

##### `accounts/ldap_sync.py`

```python
"""LDAP 同期処理本体（仕様書 §5.4）。

django-auth-ldap の populate_user シグナル経由で呼ばれる関数群。
DB 書き込みのみで認証フローには関与しない（責務分離）。
"""

import logging

from django.core.exceptions import ValidationError

from .constants import AuthSource
from .models import CustomUser, Department, LdapGroup

logger = logging.getLogger(__name__)


def sync_ldap_user(user, ldap_user_info):
    """LDAP 認証成功時に呼ばれ、CustomUser の各フィールドを同期する。

    [性質] 副作用あり（DB 書込）
    [入力] user: CustomUser（django-auth-ldap が既に作成・取得済み）
           ldap_user_info: django_auth_ldap.backend._LDAPUser（属性取得用）
    [出力] None
    [例外] ValidationError（username 衝突時）
    """
    _check_username_collision(user)

    is_new = user._state.adding

    if is_new:
        # 新規作成時のみ初期化
        user.auth_source = AuthSource.LDAP
        user.role = None  # 管理者承認待ち
        user.ldap_dn = ldap_user_info.dn
        user.set_unusable_password()  # ★ セキュリティ: ローカル認証経由のログイン防止
        # is_active は ldap_user_info の disabled 属性から判定
        user.is_active = _get_ldap_active_state(ldap_user_info, default=True)
    else:
        # 既存更新時: is_active は disabled 方向のみ同期
        ldap_active = _get_ldap_active_state(ldap_user_info, default=None)
        if ldap_active is False:
            user.is_active = False
        # ldap_active is True / None なら user.is_active は触らない

    # email, name, department は新規・既存とも LDAP から反映
    user.email = ldap_user_info.attrs.get('mail', [''])[0]
    user.first_name = ldap_user_info.attrs.get('givenName', [''])[0]
    user.last_name = ldap_user_info.attrs.get('sn', [''])[0]

    # Department 同期
    dept = sync_ldap_department(ldap_user_info)
    if dept is not None:
        user.department = dept

    user.save()


def sync_ldap_department(ldap_user_info):
    """LDAP user の department 属性から Department を upsert。

    [性質] 副作用あり（DB 書込）
    [入力] ldap_user_info
    [出力] Department | None（取得不可なら None）
    """
    dept_name = ldap_user_info.attrs.get('department', [None])[0]
    dept_code = ldap_user_info.attrs.get('departmentNumber', [None])[0]
    if dept_name is None and dept_code is None:
        return None

    # code を優先キーとして突合、なければ name で突合
    if dept_code:
        dept, _ = Department.objects.update_or_create(
            code=dept_code,
            defaults={
                'name': dept_name or dept_code,
                'auth_source': AuthSource.LDAP,
            }
        )
    else:
        dept, _ = Department.objects.update_or_create(
            name=dept_name,
            defaults={'auth_source': AuthSource.LDAP}
        )
    return dept


def sync_ldap_groups(user, ldap_user_info):
    """LDAP user の memberOf 属性から LdapGroup を upsert し M2M に反映。

    [性質] 副作用あり（DB 書込）
    [入力] user: CustomUser、ldap_user_info
    [出力] None
    """
    member_of = ldap_user_info.attrs.get('memberOf', [])
    ug_ids = []
    for dn in member_of:
        ug, _ = LdapGroup.objects.update_or_create(
            ldap_dn=dn,
            defaults={
                'name': _extract_cn_from_dn(dn),
                'auth_source': AuthSource.LDAP,
            }
        )
        ug_ids.append(ug.pk)
    user.ldap_groups.set(ug_ids)


def _check_username_collision(user):
    """同 username の auth_source='local' ユーザが既存ならエラー。"""
    if user._state.adding:
        # 新規作成時のみチェック
        if CustomUser.objects.filter(
            username=user.username,
            auth_source=AuthSource.LOCAL,
        ).exists():
            logger.error(
                f"LDAP user creation blocked: username '{user.username}' "
                f"already exists as a local user."
            )
            # 管理者通知（v1.5.x で email 通知等を実装する場合はここ）
            raise ValidationError(
                f'同名のローカルユーザが既に存在します: {user.username}'
            )


def _get_ldap_active_state(ldap_user_info, default=True):
    """LDAP 属性から is_active 相当を判定。

    AD: userAccountControl の 0x2 (ACCOUNTDISABLE) ビット
    389DS: nsAccountLock = 'true' なら無効
    取得不可なら default を返す。
    """
    attrs = ldap_user_info.attrs
    if 'userAccountControl' in attrs:
        uac = int(attrs['userAccountControl'][0])
        return not bool(uac & 0x2)
    if 'nsAccountLock' in attrs:
        return attrs['nsAccountLock'][0].lower() != 'true'
    return default


def _extract_cn_from_dn(dn):
    """LDAP DN から CN 値を抽出（'cn=foo,ou=bar' → 'foo'）。"""
    for part in dn.split(','):
        if part.strip().lower().startswith('cn='):
            return part.split('=', 1)[1].strip()
    return dn
```

##### `accounts/signals.py`

```python
"""django-auth-ldap シグナルフック（仕様書 §5.4）。"""

from django.dispatch import receiver
from django_auth_ldap.backend import populate_user

from .ldap_sync import sync_ldap_user, sync_ldap_groups


@receiver(populate_user)
def on_populate_user(sender, user, ldap_user, **kwargs):
    """django-auth-ldap が User を作成・取得した直後に発火するシグナル。

    user は未 save の状態。sync_ldap_user 内で save される。
    sync_ldap_groups は user.save() 後に呼ぶ必要があるため、save 後に呼ぶ。
    """
    sync_ldap_user(user, ldap_user)
    sync_ldap_groups(user, ldap_user)
```

##### `accounts/apps.py`

```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from . import signals  # noqa: F401
```

#### バッチ同期の扱い

v1.5.0 では**認証時の単発同期のみ**実装。バッチ同期コマンド（`./manage.py sync_ldap`）は v1.5.x 以降で別途仕様化。

### 5.5 ローカル管理者の保持

- `createsuperuser` で作成したローカル管理者は `auth_source='local'`
- LDAP 障害時もログイン可能
- LDAP 同期処理はローカルユーザを触らない（`auth_source='ldap'` のみ対象）

---

## 6. 認可判定の三段構造

```
1. user.role              ← 「業務上の肩書き」（UI/業務語彙の表示用）
                             │
                             │ default_groups で繋がる（Role 変更時のみ自動付け替え、§8 参照）
                             ▼
2. user.groups            ← 「権限の束」（auth.Group + GroupProfile）
                             │
                             │ Group.permissions
                             ▼
3. user.has_perm()        ← 「個別の能力」（最終判定、業務ロジックでの権限判定はこれを使う）
```

- **業務ロジックでの権限判定は `user.has_perm()` を使う**（Role.code の直接判定は避ける）
- Role は 1 段目のラベル、UI 表示・業務分類用
- AccessList（v1.6+）は別軸（データレベル権限）として並走

---

## 7. v1.5.0 で守る「将来用の余白」4 方針

### 7.1 クエリを 1 箇所に集約する

✗ NG: 各 View が `BusinessCard.objects.all()` を直接呼ぶ
✓ OK: `BusinessCardService.list_for(user)` 経由

### 7.2 パーミッションチェックの呼び出し口

✗ NG: テンプレートで直接 `{% if user.is_staff %}`
✓ OK: `obj.can_be_viewed_by(user)` / `request.user.has_perm('cards.view_businesscard')` 経由

**N+1 防止**: リスト表示はサービス層／QuerySet で事前フィルタ、詳細表示でのみオブジェクトメソッド。

### 7.3 「コンテナの未来」を意識する

v1.5.0 では `BusinessCardList`（コンテナ）を作らなくてもよいが、将来「全カード → 全社共通カードリストに所属」のデータ移行ができる形にしておく。

### 7.4 Permission の名前空間を分ける

- `cards.create_card` / `cards.edit_card` / `cards.merge_card` ← v1.5.0
- `persons.merge_person` / `persons.undo_merge` / `persons.link_user` ← v1.5.0
- `cards.manage_card_list` / `access_lists.edit_access_list` ← v1.6+

---

## 8. PermissionGroup のプリセット運用

```python
# apps/accounts/services.py
from django.db import transaction


def apply_role(user, role):
    """Role を付与し、対応する PermissionGroup を自動設定。

    ⚠️ この関数は Role が変わったときにだけ呼ぶこと。
    Role が変わっていない単なる保存で呼ぶと、手動で追加した Group がサイレントに消える。
    呼び出し側でガードする（CustomUserAdmin.save_model 参照）。

    ⚠️ user.save() を内部で呼ぶため、CustomUser の post_save シグナルでさらに
    apply_role() を呼ぶような実装は禁止（無限ループ回避）。

    ⚠️ Migration からは呼べない（historical model 制約）。
    Migration では下記「Migration からの呼び出し」参照。
    """
    with transaction.atomic():
        user.role = role
        user.save(update_fields=['role'])
        if role is None:
            user.groups.clear()
        else:
            user.groups.set(role.default_groups.all())
```

### Admin での発火制御

§4.1 の `CustomUserAdmin.save_model()` でガード済み（Role 変更時のみ `apply_role()` を呼ぶ）。

`user.role = X; user.save()` を**直接**書くと apply_role が呼ばれず Groups が同期されない。`apply_role()` を**必ず明示的に呼ぶ**こと。

### 運用例

| Role | default_groups |
| --- | --- |
| 管理者 (`admin`) | `card_admin`, `person_admin`, `user_admin` |
| 営業 (`sales`) | `card_editor`, `person_editor` |
| 閲覧者 (`viewer`) | `card_viewer`, `person_viewer` |

### ★ Migration からの呼び出し（v1.1 で追加）

`apply_role()` は **Migration からは呼べない**。Django Migration は `historical model` を使うため、`accounts.services.apply_role` を import すると現行モデルクラスを使ってしまい、Migration 実行時の整合性が崩れる。

Migration では以下のように**直接 Groups をセット**する:

```python
# accounts/migrations/00XX_assign_admin_role_to_superuser.py
from django.db import migrations


def forward(apps, schema_editor):
    """既存 superuser に「管理者」Role を付与し、対応する Groups もセットする。"""
    User = apps.get_model('accounts', 'CustomUser')
    Role = apps.get_model('accounts', 'Role')

    try:
        admin_role = Role.objects.get(code='admin')
    except Role.DoesNotExist:
        # admin Role がまだ作られていない → 初期データ Migration を先に流す前提
        # 何もしないで return（後続の初期データ Migration で対応）
        return

    admin_group_ids = list(admin_role.default_groups.values_list('id', flat=True))

    for su in User.objects.filter(is_superuser=True, is_active=True):
        if su.role_id is None:
            su.role = admin_role
            su.save()
            # M2M をセット（apply_role の atomic 部分を Migration 内で再現）
            su.groups.set(admin_group_ids)


def reverse(apps, schema_editor):
    """ロールバック時は role を None に戻す。"""
    User = apps.get_model('accounts', 'CustomUser')
    Role = apps.get_model('accounts', 'Role')
    try:
        admin_role = Role.objects.get(code='admin')
    except Role.DoesNotExist:
        return
    for su in User.objects.filter(role=admin_role, is_superuser=True):
        su.role = None
        su.groups.clear()
        su.save()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '00XX_create_initial_roles_and_groups'),  # 初期データ Migration（§13.8）
    ]
    operations = [
        migrations.RunPython(forward, reverse),
    ]
```

**重要なポイント**:
- `apps.get_model()` で historical model を取得（`from accounts.models import CustomUser` は NG）
- `apply_role()` の代わりに `user.groups.set()` を直接呼ぶ
- 初期データ Migration（§13.8）に依存させて、Role / Group が先に作成されていることを保証

---

## 9. ロードマップ

### v1.4.x（v1.5.0 着手前のメンテナンスタスク）

- **CustomUser モデル先行切替**（§4.1 参照）
  - `accounts/models.py` に最小定義
  - `settings.AUTH_USER_MODEL` 設定
  - DB リセット + テスト全件 PASS 確認

### v1.5.0

- CustomUser にフィールド追加
- Role, GroupProfile, Department, LdapGroup モデル
- LDAP 連携（django-auth-ldap、§5.4 の実装場所参照）
- `Person.managed_by` / `Contact.managed_by` 追加
- `User.person` OneToOneField 追加
- 既存マージサービスへの権限拡張（§13）
- `link_user_to_person()` / `unlink_user_from_person()` / `retire_user()` サービス
- ホーム画面アラート（§12.4 参照）
- 紐付け／解除画面（§12.7 参照）
- 退職処理 UI（Admin actions + 専用 View 両方、§4.1 / §12.6 参照）
- `persons.undo_merge` 等の Permission 定義
- 初期データ Migration（§13.8）
- 「将来用の余白」4 方針の徹底

### v1.6.0（または v1.5.x）

- アクセスリスト本体
- BusinessCardList コンテナ概念導入
- **`UserGroup` モデル新規追加**（業務管理者用横断グループ、AccessList のエントリ型として利用）
- LdapGroup の UI / Admin 編集機能（v1.5.0 ではテーブル + 同期のみ）
- `Department.descendants()` を django-mptt / treebeard / CTE に置換
- 制限閲覧者（freeBusyReader）の最終判断

### v1.7.0 以降

- レコードレベルの追加制約
- Google Calendar / CalDAV と同じ「コンテナ + 出席者の 2 軸モデル」を踏襲

---

## 10. 残された論点

### 10.1 確定済み（参照先あり）

| # | 論点 | 確定内容 | 参照 |
| --- | --- | --- | --- |
| 1 | Role の `default_groups` 自動同期 | `set()` で置き換え。発火は Role 変更時のみ | §8 |
| 2 | Person ⇄ User の関係 | `OneToOneField(null=True)` | §12 |
| 3 | LDAP 同期での `is_active` 扱い | 新規時のみ初期化、既存更新時は disabled 方向のみ同期 | §5.4 |
| 4 | `apply_role()` の発火条件 | Role が変わったときのみ。Migration では直接 set 使用 | §8 |
| 5 | LDAP 由来レコードの Admin 編集 | 読み取り専用化 | §4.3-4.5 |
| 6 | ローカル / LDAP の username 衝突 | LDAP 側作成スキップ + エラーログ | §5.4 |
| 7 | Department の階層展開 | `descendants()` 提供。v1.5.0 では限定的に使用 | §4.4 |
| 8 | 監査ログ | 既存 `ActionLog.record()` を使う | §12 / §13 |
| 9 | マージ機構 | v1.4.2 実装をそのまま使う | §13 |
| 10 | 1 階層復元 | `PersonMergeLog.lock_past_logs()` 既存実装 | §13 |
| 11 | アーカイブ表現 | 既存 `Person.status` enum | §12 |
| 12 | Contact のマージ追跡 | 既存 `previous_person` / `previous_status` | §13 |
| 13 | AUTH_USER_MODEL 切替タイミング | v1.4.2 開発中に先行実施 | §4.1 |
| 14 | `_can_undo_merge` の判定方法 | Permission ベース | §13.3 |
| 15 | 退職時 User.person 紐付け | 維持(各機能で `user.is_active` フィルタ) | §12.6 |
| 16 | **LDAP 同期処理の実装場所** | **`accounts/ldap_sync.py` + `accounts/signals.py`** | §5.4 |
| 17 | **ホーム画面アラートの UI 実装** | テンプレ構造・URL・遷移すべて明示 | §12.4 |
| 18 | **紐付け／解除画面の View 設計** | URL + View クラスサンプル提供 | §12.7 |
| 19 | **View 層 vs Service 層の責務分担** | View 層は粗い `has_perm()`、Service 層は `can_*` で詳細判定 | §13.7 |
| 20 | **初期データ Migration の具体例** | Group / Role / Permission 作成のコード例提供 | §13.8 |
| 21 | **退職処理 UI** | Admin actions + 専用 View 両方実装 | §4.1 / §12.6 |

### 10.2 未確定（v1.5.x 以降で判断）

| 論点 | 内容 |
| --- | --- |
| LDAP の `manager` 属性の利用可否 | 組織図機能・承認フロー用、v1.5.0 では未使用 |
| `is_default=True` の PermissionGroup を 1 つに制限するか | DB 制約 vs アプリロジック、v1.5.0 では未使用 |
| ホーム画面アラート「私はこの候補と別人です」ボタン | UX 評価後に判断 |
| バッチ LDAP 同期コマンド | 本番運用後に必要性を再評価 |

---

## 11. 関連ドキュメント

- `freegroup_accesslist_調査結果.md` — FreeGroup PHP/Laravel 版アクセスリスト調査
- `freegroup2_v1.4.2_仕様書.md`（仮称） — v1.4.2 完成版仕様書
- v1.4.2 実装コード:
  - `persons/models.py`
  - `contacts/models.py`
  - `duplicates/models.py`
  - `duplicates/services/merge_executor.py`（D-4d-1 第 3 弾改訂含む）
  - `duplicates/services/duplicate_check.py`
  - `duplicates/services/duplicate_score.py`
  - `actionlogs/models.py`

---

## 12. User-Person 紐付け仕様（v1.4.2 既存実装を尊重した簡略版）

### 12.1 全体構造（v1.4.2 既存）

```
[CustomUser]              ← v1.4.x で先行切替、v1.5.0 でフィールド追加
   │ OneToOne (nullable, v1.5.0 で新規追加)
   ▼
[Person]                  ← v1.4.2 既存
   │ 1:N
   ▼
[Contact]                 ← v1.4.2 既存
```

### 12.2 v1.5.0 で追加するフィールド（マイグレーション）

#### Person.managed_by

```python
# persons/migrations/00XX_add_managed_by.py
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('persons', '0XXX_previous_migration'),       # v1.4.2 の最新マイグレーション番号
        ('accounts', '0001_initial'),                 # CustomUser を作成するマイグレーション
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='Person',
            name='managed_by',
            field=models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True, blank=True,
                on_delete=models.SET_NULL,
                related_name='persons_managed',
            ),
        ),
    ]
```

`Contact.managed_by` も同様。

### 12.3 紐付けの基本ルール

| 項目 | 仕様 |
| --- | --- |
| カーディナリティ | 1 User : 0..1 Person（OneToOne nullable） |
| FK の向き | `User.person`（逆参照は `person.linked_user` 経由必須） |
| 自動生成 | しない |
| 紐付け実行者 | 本人 + 管理者 |
| 解除実行者 | 本人 + 管理者 |
| 既に別 Person に紐付き済の User への変更 | エラー |

#### `Person.linked_user` プロパティ（循環依存回避版）

```python
# persons/models.py に追加
from django.core.exceptions import ObjectDoesNotExist


class Person(models.Model):
    # ... 既存フィールド ...

    @property
    def linked_user(self):
        """User との紐付け取得。未紐付け時は None。

        循環依存回避のため、CustomUser を直接 import せず ObjectDoesNotExist で catch。
        accounts.CustomUser に依存しないので persons アプリ単体でテスト可能。
        """
        try:
            return self.user
        except ObjectDoesNotExist:
            return None
```

**重要**: コード君は **`person.user` への直接アクセスを避け**、必ず `person.linked_user` 経由で参照すること。

### 12.4 ホーム画面アラート（自主紐付け促進）★ v1.1 で UI 実装方針追加

LDAP からの自動紐付けは行わない。代わりにホーム画面で**本人に通知**し、本人の意思で紐付けてもらう。

#### 表示条件

- `User.person is None`（未紐付け）
- `User.email` と同じ `email` を持つ Contact が存在
- その Contact が指す Person はまだ User と紐付いていない

#### 表示頻度

**紐付くまで毎回ログイン時に表示**。「後で」ボタンは用意しない。

#### 複数候補時の挙動

同じ email を持つ Contact が複数 Person を指す場合 → 「先に Person マージで重複を整理してください」と促す。

#### 定数定義

```python
# apps/accounts/constants.py に追加
class PersonLinkStatus:
    """ホーム画面アラートのステータス値（テンプレート側でも参照）。"""
    SINGLE_CANDIDATE = 'single_candidate'
    MULTIPLE_CANDIDATES_NEED_MERGE = 'multiple_candidates_need_merge'
```

#### View 実装（ORM 完結）

```python
# home/views.py（v1.5.0 拡張。v1.4.2 に既存の HomeView があれば拡張、なければ新規）
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from contacts.models import Contact
from persons.models import Person
from accounts.constants import PersonLinkStatus


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = 'home/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        if user.person is None and user.email:
            # ★ ORM 完結: person__user__isnull=True で OneToOne 逆参照を直接フィルタ
            #   （Python 側ループを排除、N+1 回避）
            candidates_qs = Contact.objects.filter(
                email=user.email,
                person__status=Person.Status.ACTIVE,
                person__user__isnull=True,
            ).select_related('person')
            # 注: candidates の各 Contact について candidate.person.user は必ず存在しない
            # （フィルタで person__user__isnull=True を指定しているため）。
            # テンプレートで candidate.person.user を参照すると例外が出るので避けること。
            candidates = list(candidates_qs)

            distinct_persons = {c.person_id for c in candidates}
            if len(distinct_persons) > 1:
                ctx['person_link_status'] = PersonLinkStatus.MULTIPLE_CANDIDATES_NEED_MERGE
                ctx['person_link_candidates'] = candidates
            elif distinct_persons:
                ctx['person_link_status'] = PersonLinkStatus.SINGLE_CANDIDATE
                ctx['person_link_candidates'] = candidates

        return ctx
```

#### UI 実装方針（テンプレート / URL / 遷移）★ v1.1 で追加

##### テンプレート構造

`templates/home/home.html` に既存 `app-alert` クラスでアラートブロックを追加:

```html
{# 既存 home.html の冒頭付近に追加 #}
{% if person_link_status == 'single_candidate' %}
  {% for candidate in person_link_candidates %}
    <div class="app-alert app-alert--info">
      <p>あなたの名刺データが見つかりました: <strong>{{ candidate.full_name }}</strong></p>
      <form method="post" action="{% url 'accounts:link_user_person' user_id=request.user.id person_id=candidate.person_id %}">
        {% csrf_token %}
        <button type="submit" class="app-button app-button--primary">紐付ける</button>
      </form>
    </div>
  {% endfor %}

{% elif person_link_status == 'multiple_candidates_need_merge' %}
  <div class="app-alert app-alert--warning">
    <p>あなたの可能性がある Person が複数見つかりました。</p>
    <p>先に <a href="{% url 'duplicates:duplicate_group_list' %}">Person のマージ画面</a> で重複を整理してください。</p>
    <ul>
      {% for candidate in person_link_candidates %}
        <li>{{ candidate.full_name }} ({{ candidate.email }})</li>
      {% endfor %}
    </ul>
  </div>
{% endif %}
```

##### 確認画面の有無

- **確認画面は挟まない**（紐付けは可逆な操作のため）
- 直接 POST で `link_user_to_person()` を呼ぶ
- 紐付けは別画面（§12.7 専用 View）でも実行可能なので、ホーム画面のは「即実行」のショートカット

##### POST 後の挙動

- 成功時: ホーム画面にリダイレクト + Django messages framework で成功通知
- 失敗時（ValidationError）: ホーム画面にリダイレクト + エラー通知

URL ルートは §12.7 参照。

### 12.5 LDAP 同期は紐付けに関与しない

- LDAP は User の作成・更新のみ
- `User.person` には絶対に手を触れない
- LDAP の email が変わっても既存紐付けは不変

### 12.6 退職処理（managed_by 引き継ぎ）★ v1.1 で UI 両方実装

#### サービス層（既存 v1.0 と同じ）

```python
# apps/accounts/services.py
from django.core.exceptions import ValidationError
from django.db import transaction
from accounts.constants import ActionLogAction


def retire_user(user, successor):
    """退職処理。managed_by を後継者に一括引き継ぎ。

    user.is_active = False にする。User.person 紐付けは維持（履歴として）。
    """
    if user.pk == successor.pk:
        raise ValidationError('退職者と後継者が同じです')
    if not successor.is_active:
        raise ValidationError('後継者は現職者でなければなりません')

    with transaction.atomic():
        user.persons_managed.update(managed_by=successor)
        user.contacts_managed.update(managed_by=successor)
        user.is_active = False
        user.save(update_fields=['is_active'])

        from actionlogs.models import ActionLog
        ActionLog.record(
            user=successor,
            action=ActionLogAction.RETIRE_USER,
            content_object=user,
            object_repr=f"{user.username} → {successor.username}",
            data={'retired_user_id': str(user.pk), 'successor_id': str(successor.pk)},
        )
```

#### UI 実装（★ v1.1 で追加: Admin actions + 専用 View 両方）

退職処理は管理者業務なので、以下の **両方**を実装する:

1. **Admin actions**: バッチ的に複数ユーザを退職処理（HR 担当が Admin で一括処理）
2. **専用 View**: 個別ユーザの退職処理（一般 UI フロー、管理者ロール限定）

##### A. Admin actions

§4.1 の `CustomUserAdmin.retire_user_action()` を参照。インターメディエイト画面で後継者を選択し、確定で `retire_user()` を呼ぶ。

テンプレート `templates/admin/accounts/retire_user_intermediate.html`:

```html
{% extends "admin/base_site.html" %}

{% block content %}
  <h1>退職処理</h1>
  <form method="post" action="">
    {% csrf_token %}
    <p>以下のユーザを退職処理します:</p>
    <ul>
      {% for user in users %}
        <li>{{ user.username }} ({{ user.get_full_name }})</li>
        <input type="hidden" name="_selected_action" value="{{ user.pk }}">
      {% endfor %}
    </ul>

    <p>後継者を選択してください:</p>
    <select name="successor" required>
      {% for u in successor_choices %}
        <option value="{{ u.pk }}">{{ u.username }} ({{ u.get_full_name }})</option>
      {% endfor %}
    </select>

    <input type="hidden" name="action" value="{{ action }}">
    <input type="hidden" name="select_across" value="{{ select_across }}">
    <button type="submit" name="apply" value="1" class="default">退職処理を実行</button>
    <a href="{% url 'admin:accounts_customuser_changelist' %}">キャンセル</a>
  </form>
{% endblock %}
```

##### B. 専用 View（管理者業務 UI）

```python
# apps/accounts/views.py
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from .models import CustomUser
from .services import retire_user


class RetireUserView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """退職処理の専用 View（管理者業務 UI）。

    GET: 後継者選択フォーム表示
    POST: retire_user() 実行 → ユーザ管理一覧にリダイレクト
    """
    permission_required = 'accounts.retire_user'  # 専用 Permission（§13.8 で定義）
    template_name = 'accounts/retire_user.html'

    def get(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        successor_choices = CustomUser.objects.filter(is_active=True).exclude(pk=user_id)
        return render(request, self.template_name, {
            'target_user': target_user,
            'successor_choices': successor_choices,
        })

    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        successor_id = request.POST.get('successor')
        successor = get_object_or_404(CustomUser, pk=successor_id)
        try:
            retire_user(user=target_user, successor=successor)
            messages.success(request, f'{target_user.username} の退職処理を完了しました')
        except Exception as e:
            messages.error(request, f'退職処理に失敗しました: {e}')
        return redirect('accounts:user_list')
```

URL ルート（§12.7 末尾参照）。

### 12.7 紐付け／解除画面の View 設計（★ v1.1 で追加）

#### サービス層（既存 v1.0 と同じ）

```python
# apps/accounts/services.py
from django.core.exceptions import ValidationError
from django.db import transaction
from accounts.constants import ActionLogAction


def link_user_to_person(operator, user, person):
    """User と Person を紐付ける。

    [権限チェック責務]
    本関数は権限チェックを行わない。呼び出し側 (View) で以下を保証すること:
    - operator == user (本人) または
    - operator.has_perm('accounts.link_user_to_person') (管理者権限)

    [両側ガード] OneToOne 制約違反防止のため、user 側・person 側両方をチェック。
    """
    if user.person is not None:
        raise ValidationError(
            f'User {user.username} は既に別の Person に紐付いています。'
            '先に既存紐付けを解除してください'
        )

    existing_user = person.linked_user
    if existing_user is not None and existing_user != user:
        raise ValidationError(
            f'Person は既に User ({existing_user.username}) に紐付いています。'
            '先に既存紐付けを解除してください'
        )

    with transaction.atomic():
        user.person = person
        user.save(update_fields=['person'])

        from actionlogs.models import ActionLog
        ActionLog.record(
            user=operator,
            action=ActionLogAction.LINK_USER_TO_PERSON,
            content_object=user,
            object_repr=f"{user.username} ↔ Person({person.pk})",
            data={'person_id': str(person.pk)},
        )


def unlink_user_from_person(operator, user):
    """User と Person の紐付けを解除する。

    [権限チェック責務] link_user_to_person() と同様、呼び出し側で保証。
    """
    if user.person is None:
        return

    person = user.person
    with transaction.atomic():
        user.person = None
        user.save(update_fields=['person'])

        from actionlogs.models import ActionLog
        ActionLog.record(
            user=operator,
            action=ActionLogAction.UNLINK_USER_FROM_PERSON,
            content_object=user,
            object_repr=f"{user.username} ↮ Person({person.pk})",
            data={'person_id': str(person.pk)},
        )
```

#### View 実装サンプル（★ v1.1 で追加）

```python
# apps/accounts/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from .models import CustomUser
from .services import link_user_to_person, unlink_user_from_person
from persons.models import Person


class LinkUserPersonView(LoginRequiredMixin, View):
    """User と Person を紐付ける View。

    権限: 本人（request.user == target_user）または accounts.link_user_to_person Permission。
    POST のみ受け付ける。
    """
    def post(self, request, user_id, person_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        person = get_object_or_404(Person, pk=person_id)

        # 権限チェック（View 層）: 本人または管理者
        if request.user != target_user and not request.user.has_perm('accounts.link_user_to_person'):
            raise PermissionDenied('紐付けの権限がありません')

        try:
            link_user_to_person(operator=request.user, user=target_user, person=person)
            messages.success(request, '紐付けました')
        except ValidationError as e:
            messages.error(request, str(e))

        # ホーム画面に戻る（ホーム画面アラートからの遷移を想定）
        return redirect('home')


class UnlinkUserPersonView(LoginRequiredMixin, View):
    """User と Person の紐付けを解除する View。"""
    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)

        # 権限チェック（View 層）: 本人または管理者
        if request.user != target_user and not request.user.has_perm('accounts.link_user_to_person'):
            raise PermissionDenied('紐付け解除の権限がありません')

        try:
            unlink_user_from_person(operator=request.user, user=target_user)
            messages.success(request, '紐付けを解除しました')
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect('home')
```

#### URL 設計

```python
# apps/accounts/urls.py
from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # 紐付け／解除
    path('users/<int:user_id>/link/<uuid:person_id>/',
         views.LinkUserPersonView.as_view(),
         name='link_user_person'),
    path('users/<int:user_id>/unlink/',
         views.UnlinkUserPersonView.as_view(),
         name='unlink_user_person'),

    # 退職処理（専用 View、§12.6 B）
    path('users/<int:user_id>/retire/',
         views.RetireUserView.as_view(),
         name='retire_user'),

    # ユーザ管理（Phase 6 で UI 実装）
    path('users/',
         views.UserListView.as_view(),
         name='user_list'),
    path('users/<int:user_id>/',
         views.UserDetailView.as_view(),
         name='user_detail'),

    # ... 他の URL ...
]
```

### 12.8 業務動機（参考）

User-Person 紐付けが必要な理由（v1.6+ で実装予定）:

| 機能 | 紐付けが必要な理由 |
| --- | --- |
| メールマーケティング | 自社社員の名前と社内 email を From にメール自動生成 |
| メルマガ配信 | 担当社員の email を送信元にした営業メール |
| 案件管理 | 担当社員（User）と相手先（Person/Contact）の紐付け |

各機能で `user.is_active=True` フィルタを使い、退職者を除外する。

---

## 13. 既存マージサービスへの権限拡張（v1.5.0 で追加する差分）

v1.4.2 の既存実装は**そのまま動作する状態を保ち**、v1.5.0 では権限チェックを差分挿入する。

**v1.4.2 実装の前提**: `Execute_Merge_Only` は **D-4d-1 第 3 弾改訂**で **`with transaction.atomic():` を関数冒頭から包む構造**になっている。

### 13.1 `can_merge_person()` 権限判定ヘルパ

```python
# apps/accounts/services.py
def can_merge_person(operator, person):
    """Person をマージできる権限があるか判定（5' ルール拡張版）。

    - User 未紐付け Person: 通常権限で判定（True）
    - 紐付き User 本人が現職: True
    - 退職者の Person: managed_by の現職者が代行可
    - その他: False
    """
    linked_user = person.linked_user

    if linked_user is None:
        return True
    if linked_user.is_active and linked_user == operator:
        return True
    if not linked_user.is_active and person.managed_by_id == operator.id:
        return True
    return False
```

### 13.2 `Execute_Merge_Only` への組み込み（差分）

(v1.0 §13.2 と同じ内容。コード例は省略、v1.0 を参照)

順序の意図:
1. Form 読み出し（既存）
2. 権限チェック（v1.5.0 で追加、atomic 外）
3. `with transaction.atomic():` 開始（既存、D-4d-1 第 3 弾改訂で冒頭包み）
4. CFC 確定処理（既存）
5. バリデーション（既存）
6. PersonMergeLog 作成（既存、手順 5）
7. transfer_contacts_to（既存、手順 6）
8. **User 紐付け引き継ぎ（v1.5.0 で追加）** — transfer_contacts_to の後、mark_as_merged の前
9. mark_as_merged（既存、手順 7）
10. lock_past_logs（既存、手順 8）
11. candidate.mark_as_merged（既存、手順 10 前段、recover の前）
12. recover_duplicate_candidates（既存、手順 9）
13. duplicate_checked_at 更新（既存、手順 9 末）
14. record_merge_action（既存、手順 10）

詳細コードは v1.0 §13.2 参照。

### 13.3 `Execute_Merge_Undo` への組み込み（差分）

```python
def Execute_Merge_Undo(merge_log, form, user):
    # ★ v1.5.0 で追加: 復元権限チェック（atomic 外）
    if not _can_undo_merge(user, merge_log):
        raise PermissionDenied('マージを復元する権限がありません')
    # (以下、v1.4.2 既存処理そのまま)
    ...


def _can_undo_merge(user, merge_log):
    """マージ復元権限の判定（Permission ベース、§6 三段構造と整合）。"""
    if user.is_superuser:
        return True
    if merge_log.executed_by_id == user.id:
        return True
    if user.has_perm('persons.undo_merge'):
        return True
    return False
```

### 13.4 `Mark_as_Different_Person` への組み込み（差分）

```python
def Mark_as_Different_Person(candidate, form, user):
    _check_different_person_permission(user, candidate.person_a, candidate.person_b)
    # (以下、v1.4.2 既存処理そのまま)
    ...
```

### 13.5 DuplicateCandidate 生成への「両方 User 紐付き」ガード

```python
# apps/accounts/services.py
def get_excluded_persons_for_user_linked(person):
    """User 紐付き Person の場合、他の User 紐付き Person を除外対象として返す。

    ⚠️ パフォーマンス注意: 全社員数が増えると返り値も増える。
    1000 件超で find_duplicate_contacts のクエリパフォーマンスが劣化する場合、
    excluded_persons を ID リストではなく Subquery で渡すよう
    呼び出し側を変更する検討が必要。
    """
    linked_user = person.linked_user
    if linked_user is None:
        return []
    from persons.models import Person
    return list(
        Person.objects
        .filter(user__isnull=False)
        .filter(status=Person.Status.ACTIVE)
        .exclude(pk=person.pk)
    )


def generate_duplicate_candidates_for_contact(contact):
    """重複候補生成の呼び出し例（v1.5.0 改修）。

    前提: contact は呼び出し元 で
    select_for_update(skip_locked=True).prefetch_related('confidences') 済み
    """
    from duplicates.services.duplicate_check import (
        find_duplicate_contacts,
        get_persons_confirmed_as_different,
    )
    from accounts.services import get_excluded_persons_for_user_linked

    excluded = []
    excluded.extend(get_persons_confirmed_as_different(contact.person))
    excluded.extend(get_excluded_persons_for_user_linked(contact.person))

    results = find_duplicate_contacts(contact, excluded_persons=excluded)
    # ... 以下 v1.4.2 既存の DuplicateCandidate 作成ロジック ...
```

### 13.6 確定した運用ルール総まとめ

```
[紐付け]
  - 本人 + 管理者が実行可
  - 既存紐付けがある User への変更はエラー
  - ホーム画面で email マッチ候補をアラート表示（ORM 完結クエリ）
  - 複数候補時は先に Person マージで整理

[解除]
  - 本人 + 管理者が実行可
  - 退職時は紐付け維持（履歴として、各機能で is_active フィルタ）

[LDAP 同期]
  - User の作成・更新のみ
  - User.person は触らない
  - is_active は disabled 方向のみ同期、enabled は触らない
  - 実装場所: accounts/ldap_sync.py + accounts/signals.py（§5.4 参照）

[マージ] ★ v1.4.2 既存実装を尊重 + v1.5.0 で権限チェック挿入
  - 既存 Execute_Merge_Only / Execute_Merge_Undo / Mark_as_Different_Person をそのまま使う
  - 権限チェックを atomic の外に挿入
  - 5' ルール拡張: 紐付き User 本人 or 退職時は managed_by の現職者
  - 両方 User 紐付きは禁止（多重防衛）
  - User 紐付け引き継ぎは transfer_contacts_to の後・mark_as_merged の前で実行
  - _can_undo_merge は Permission ベース

[DuplicateCandidate]
  - 既存 find_duplicate_contacts / determine_score_and_rank をそのまま使う
  - 「両方 User 紐付き」除外は excluded_persons 経由

[退職処理]
  - retire_user(user, successor) サービス
  - UI: Admin actions + 専用 View 両方（§4.1 / §12.6）
  - managed_by 一括引き継ぎ
  - User.is_active = False
  - User.person は維持

[監査ログ]
  - ActionLog.record() を使う
  - ActionLogAction 定数で文字列を一元管理
```

### 13.7 ★ View 層 vs Service 層の権限チェック責務分担（v1.1 で追加）

権限チェックは**両層で二重防衛**するが、責務が異なる:

| 層 | 責務 | チェック内容 |
| --- | --- | --- |
| **View 層** | UI 制御（ボタン非表示、画面アクセス拒否） | `request.user.has_perm('app.codename')` で粗い判定 |
| **Service 層** | データレベルの権限（Person 個別の権限） | `can_merge_person(user, person)` で詳細判定 |

両層で実行することで、以下の経路すべてに防御が効く:

| 経路 | View 層 | Service 層 |
| --- | --- | --- |
| ブラウザからボタン押下 | ✅ | ✅ |
| API 直叩き | ❌（View を経由する場合は ✅） | ✅ |
| 管理コマンド / shell | ❌ | ✅ |
| 別 View からの Service 呼び出し | ❌ | ✅ |

#### 実装パターン

```python
# View 層: UI とアクセス制御
class ExecuteMergeView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'persons.merge_person'  # ← View 層の粗い判定

    def post(self, request, ...):
        ...
        # Service 層で詳細判定（権限の詳細は Service 内で）
        Execute_Merge_Only(candidate, surviving_person, merged_person, form, request.user)
        ...


# Service 層: ビジネスロジックと詳細判定
def Execute_Merge_Only(candidate, surviving_person, merged_person, form, user):
    # ★ Service 層の詳細判定
    _check_merge_permission(user, surviving_person, merged_person)
    ...
```

**役割分担のまとめ**:
- View 層は「**画面を見せていいか、操作させていいか**」を判定
- Service 層は「**この具体的なマージを実行していいか**」を判定
- 同じ Permission を View 層と Service 層で再チェックしてもパフォーマンス的に問題ない（in-memory チェックのため）

### 13.8 ★ 初期データ Migration（v1.1 で追加）

#### 命名規約（v1.5.0 で確定）

| 種別 | 命名 |
| --- | --- |
| **Role コード** | `admin` / `sales` / `viewer` |
| **Group 名** | `person_admin` / `person_editor` / `person_viewer`、`card_admin` / `card_editor` / `card_viewer`、`user_admin` 等 |
| **Permission codename** | `persons.undo_merge` / `persons.merge_person` / `persons.link_user`、`accounts.link_user_to_person` / `accounts.retire_user` 等 |

#### Group / Role / Permission の紐付け表

| Group | 含まれる Permission | 含まれる Role |
| --- | --- | --- |
| `person_admin` | `persons.undo_merge`, `persons.merge_person`, `persons.link_user` | `admin` |
| `person_editor` | `persons.merge_person`, `persons.link_user` | `sales` |
| `person_viewer` | `persons.view_person`（標準） | `viewer` |
| `user_admin` | `accounts.link_user_to_person`, `accounts.retire_user` | `admin` |
| `card_admin` | `cards.create_card`, `cards.edit_card`, `cards.merge_card` | `admin` |
| `card_editor` | `cards.create_card`, `cards.edit_card` | `sales` |
| `card_viewer` | `cards.view_businesscard`（Django 標準、モデル名 BusinessCard） | `viewer` |

#### 実装すべき Permission

```python
# persons/models.py の Person.Meta に追加
class Person(models.Model):
    # ... 既存フィールド ...

    class Meta:
        # ⚠️ 既存の indexes / ordering 等は維持し、permissions のみ追加
        # （既存 Meta を上書きしないこと）
        # 既存:
        #   indexes = [...]
        #   ordering = [...]
        # v1.5.0 で追加:
        permissions = [
            ('undo_merge',   'マージ復元を実行できる'),
            ('merge_person', 'Person マージを実行できる'),
            ('link_user',    'User-Person 紐付けを設定できる'),
        ]


# accounts/models.py の CustomUser.Meta に追加（または別途 Migration で追加）
class CustomUser(AbstractUser):
    # ... 既存フィールド ...

    class Meta:
        permissions = [
            ('link_user_to_person', 'User と Person の紐付けを管理できる'),
            ('retire_user',          'ユーザを退職処理できる'),
        ]


# cards/models.py の BusinessCard.Meta に追加（v1.5 で追加）
class BusinessCard(models.Model):
    # ... 既存フィールド ...

    class Meta:
        # ⚠️ 既存の indexes / ordering 等は維持し、permissions のみ追加
        # （既存 Meta を上書きしないこと）
        # 既存:
        #   indexes = [...]
        #   ordering = [...]
        # v1.5.0 で追加:
        permissions = [
            ('create_card', '名刺カードを作成できる'),
            ('edit_card',   '名刺カードを編集できる'),
            ('merge_card',  '名刺カードをマージできる'),
        ]
```

#### 初期データ Migration コード

```python
# accounts/migrations/00XX_create_initial_roles_and_groups.py
from django.db import migrations


def forward(apps, schema_editor):
    """Group / Role / 紐付けを初期データとして作成。

    Django の post_migrate より前に走るため、Permission レコードが未作成。
    先に create_permissions を明示的に呼んで Permission / ContentType を埋める
    （Django 公式 API による補完）。
    """
    # ⚠️ Permission を物理化（同一 migrate 実行内で AlterModelOptions 直後に
    # RunPython を呼ぶため、post_migrate シグナル待ちでは間に合わない。
    # Django の create_permissions を明示呼び出して Permission / ContentType を
    # 先に DB に書き込む。apps=apps で historical model レジストリを渡す）
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions
    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    Role = apps.get_model('accounts', 'Role')

    # ---- 1. Group を作成 ----
    person_admin, _ = Group.objects.get_or_create(name='person_admin')
    person_editor, _ = Group.objects.get_or_create(name='person_editor')
    person_viewer, _ = Group.objects.get_or_create(name='person_viewer')
    user_admin, _ = Group.objects.get_or_create(name='user_admin')
    card_admin, _ = Group.objects.get_or_create(name='card_admin')
    card_editor, _ = Group.objects.get_or_create(name='card_editor')
    card_viewer, _ = Group.objects.get_or_create(name='card_viewer')

    # ---- 2. Permission を Group に紐付け ----
    def add_perm(group, app_label, codename):
        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        group.permissions.add(perm)

    # person_admin
    add_perm(person_admin, 'persons', 'undo_merge')
    add_perm(person_admin, 'persons', 'merge_person')
    add_perm(person_admin, 'persons', 'link_user')

    # person_editor
    add_perm(person_editor, 'persons', 'merge_person')
    add_perm(person_editor, 'persons', 'link_user')

    # person_viewer
    add_perm(person_viewer, 'persons', 'view_person')  # Django 標準

    # user_admin
    add_perm(user_admin, 'accounts', 'link_user_to_person')
    add_perm(user_admin, 'accounts', 'retire_user')

    # card_admin
    add_perm(card_admin, 'cards', 'create_card')
    add_perm(card_admin, 'cards', 'edit_card')
    add_perm(card_admin, 'cards', 'merge_card')

    # card_editor
    add_perm(card_editor, 'cards', 'create_card')
    add_perm(card_editor, 'cards', 'edit_card')

    # card_viewer
    add_perm(card_viewer, 'cards', 'view_businesscard')  # Django 標準（モデル名 BusinessCard）

    # ---- 3. Role を作成 ----
    admin_role, _ = Role.objects.get_or_create(
        code='admin',
        defaults={'name': '管理者', 'sort_order': 1}
    )
    sales_role, _ = Role.objects.get_or_create(
        code='sales',
        defaults={'name': '営業', 'sort_order': 2}
    )
    viewer_role, _ = Role.objects.get_or_create(
        code='viewer',
        defaults={'name': '閲覧者', 'sort_order': 3}
    )

    # ---- 4. Role の default_groups を設定 ----
    admin_role.default_groups.set([person_admin, user_admin, card_admin])
    sales_role.default_groups.set([person_editor, card_editor])
    viewer_role.default_groups.set([person_viewer, card_viewer])


def reverse(apps, schema_editor):
    """ロールバック時は作成した Role と Group を削除。"""
    Role = apps.get_model('accounts', 'Role')
    Group = apps.get_model('auth', 'Group')

    Role.objects.filter(code__in=['admin', 'sales', 'viewer']).delete()
    Group.objects.filter(name__in=[
        'person_admin', 'person_editor', 'person_viewer', 'user_admin',
        'card_admin', 'card_editor', 'card_viewer',
    ]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '00XX_previous_migration'),  # Role モデル作成の Migration
        ('persons',  '00XX_add_permissions'),     # persons.Meta.permissions 追加 Migration
        ('cards',    '00XX_add_permissions'),     # cards.Meta.permissions 追加 Migration（v1.5）
    ]
    operations = [
        migrations.RunPython(forward, reverse),
    ]
```

#### 既存 superuser への Role 付与

§8 末尾の「Migration からの呼び出し」を参照。`assign_admin_role_to_superuser` Migration を上記初期データ Migration の**後**に流す。

#### Migration 実行順序

```
1. accounts/migrations/0001_initial.py             # CustomUser 作成
2. persons/migrations/00XX_add_managed_by.py       # Person.managed_by 追加
3. contacts/migrations/00XX_add_managed_by.py      # Contact.managed_by 追加
4. persons/migrations/00XX_add_permissions.py      # Person.Meta.permissions
5. cards/migrations/00XX_add_permissions.py        # BusinessCard.Meta.permissions（v1.5 で追加）
6. accounts/migrations/00XX_add_permissions.py     # CustomUser.Meta.permissions
7. accounts/migrations/00XX_create_initial_roles_and_groups.py  # ★ 初期データ
8. accounts/migrations/00XX_assign_admin_role_to_superuser.py   # 既存 superuser に Role 付与
```

---

## 付録 A: 概念対応マトリクス（早見表）

| 業務概念 | FreeGroup PHP | FreeGroup2 Django (v1.4.2 + v1.5.0) |
| --- | --- | --- |
| 個別能力フラグ | `role_lists.role` | `auth.Permission`（v1.5.0） |
| 能力の束 | `role_groups` | `auth.Group` + `GroupProfile`（v1.5.0） |
| 業務上の肩書き | (RoleGroup の name で兼用) | `Role` モデル（v1.5.0） |
| ユーザ | `users` | `CustomUser`（v1.4.x で先行切替、v1.5.0 でフィールド追加） |
| 部署 | `depts`（フラット） | `Department`（v1.5.0、木構造） |
| LDAP 由来グループ | （該当なし） | `LdapGroup`（v1.5.0、`memberOf` 受け皿） |
| 業務管理者用横断グループ | `groups` | **`UserGroup`（v1.6+ で AccessList と同時に新規追加）** |
| 人物 | （該当なし） | `Person`（v1.4.2 既存） |
| 名刺データ | （該当なし） | `Contact`（v1.4.2 既存） |
| 重複候補 | （該当なし） | `DuplicateCandidate`（v1.4.2 既存） |
| マージ履歴 | （該当なし） | `PersonMergeLog`（v1.4.2 既存） |
| 監査ログ | （該当なし） | `ActionLog`（v1.4.2 既存） |
| アクセスリスト | `access_lists` | （v1.6+） |

---

## 付録 B: v1.5.0 実装チェックリスト

### v1.4.x（v1.5.0 着手前）

- [ ] `accounts/models.py` に `CustomUser` 最小定義（`AbstractUser` 継承）
- [ ] `settings.AUTH_USER_MODEL = 'accounts.CustomUser'` 設定
- [ ] DB リセット + `python manage.py migrate` で再構築
- [ ] 既存テスト全件 PASS 確認

### モデル定義（v1.5.0、accounts アプリ拡張）

- [ ] `CustomUser` にフィールド追加
- [ ] `CustomUserAdmin` 定義（`get_fieldsets()` / `get_form()` / `save_model()` / `retire_user_action` ガード）
- [ ] `Role` モデル定義 + `default_groups` M2M
- [ ] `GroupProfile` モデル + Group 作成時の自動生成シグナル
- [ ] `Department` モデル定義
- [ ] `LdapGroup` モデル定義
- [ ] `AuthSource` TextChoices 定数
- [ ] `ActionLogAction` 定数クラス（accounts/constants.py）
- [ ] **`PersonLinkStatus` 定数クラス**（accounts/constants.py、§12.4 用）

### 既存モデルへのフィールド追加（マイグレーション）

- [ ] `Person.managed_by` FK 追加マイグレーション（`swappable_dependency` 含む）
- [ ] `Contact.managed_by` FK 追加マイグレーション
- [ ] `Person.linked_user` プロパティ追加（`ObjectDoesNotExist` で catch、循環依存回避）
- [ ] `Person.Meta.permissions` に `undo_merge` / `merge_person` / `link_user` 追加（既存 Meta を上書きしないこと）
- [ ] `CustomUser.Meta.permissions` に `link_user_to_person` / `retire_user` 追加
- [ ] **`cards.BusinessCard.Meta.permissions` に `create_card` / `edit_card` / `merge_card` を追加（既存 Meta を上書きしないこと）**（v1.5 で追加）

### Admin 編集ガード

- [ ] `CustomGroupAdmin.GroupProfileInline.get_readonly_fields()` で LDAP 由来は読み取り専用
- [ ] `DepartmentAdmin.get_readonly_fields()` で LDAP 由来は読み取り専用
- [ ] `LdapGroupAdmin.get_readonly_fields()` で LDAP 由来は読み取り専用

### LDAP 連携（実装場所: §5.4 参照）

- [ ] `AUTH_BACKEND` 環境変数で `local`/`ldap`/`both` を切替可能に
- [ ] `AUTHENTICATION_BACKENDS` を env 値に応じて切替
- [ ] django-auth-ldap 設定（`.env` から接続情報）
- [ ] `AUTH_LDAP_CACHE_TIMEOUT` を `LDAP_CACHE_ENABLED` / `LDAP_CACHE_TTL` から構築
- [ ] **`accounts/ldap_sync.py` 実装**（`sync_ldap_user` / `sync_ldap_department` / `sync_ldap_groups` / `_check_username_collision`）
- [ ] **`accounts/signals.py` 実装**（`populate_user` シグナルフック）
- [ ] **`accounts/apps.py` の `ready()` で signals を import**
- [ ] LDAP 同期で `is_active` は新規時のみ初期化、既存更新時は disabled 方向のみ同期
- [ ] LDAP 自動生成ユーザは `role=None`, `groups=[]`, `set_unusable_password()` で生成
- [ ] ローカル管理者保持

### Role / Permission 関連

- [ ] `apply_role(user, role)` ヘルパ実装（`transaction.atomic()` 内）
- [ ] `CustomUserAdmin.save_model()` で Role 変更時のみ `apply_role()` を呼ぶガード
- [ ] **初期データ Migration**（`accounts/migrations/00XX_create_initial_roles_and_groups.py`、§13.8 参照）
- [ ] **既存 superuser に admin Role を付与する Migration**（§8 末尾参照）

### User-Person 関連サービス（accounts/services.py）

- [ ] `link_user_to_person(operator, user, person)` 実装
- [ ] `unlink_user_from_person(operator, user)` 実装
- [ ] `retire_user(user, successor)` 実装
- [ ] `can_merge_person(operator, person)` 権限判定ヘルパ
- [ ] `get_excluded_persons_for_user_linked(person)` 実装

### 既存サービスへの権限拡張

- [ ] `Execute_Merge_Only` Form 読み出し後・atomic 外に `_check_merge_permission` 挿入
- [ ] `Execute_Merge_Only` の `transfer_contacts_to` の後・`mark_as_merged` の前に User 紐付け引き継ぎ挿入
- [ ] `Execute_Merge_Undo` 冒頭・atomic 外に `_can_undo_merge` チェック挿入
- [ ] `Mark_as_Different_Person` 冒頭・atomic 外に `_check_different_person_permission` 挿入
- [ ] `generate_duplicate_candidates_for_contact` に `get_excluded_persons_for_user_linked` 組み込み

### View / UI

- [ ] **ホーム画面アラート View 拡張**（§12.4 ORM 完結クエリ）
- [ ] **ホーム画面アラート テンプレート**（`templates/home/home.html` に `app-alert` で追加）
- [ ] **`LinkUserPersonView` / `UnlinkUserPersonView` 実装**（§12.7）
- [ ] **`RetireUserView` 専用 View 実装**（§12.6 B）
- [ ] **`accounts/urls.py` で URL ルート定義**（§12.7 末尾）
- [ ] **Admin actions `retire_user_action` 実装 + インターメディエイトテンプレート**（§4.1 / §12.6 A）
- [ ] LDAP 同期処理が `User.person` に触れないことの確認

### 「将来用の余白」4 方針

- [ ] クエリ集約(サービス層経由)
- [ ] permission 呼び出し口（View 層 + Service 層の二重防衛、§13.7 参照）
- [ ] BusinessCardList コンテナの余白
- [ ] Permission 名前空間分離

---

## 付録 C: v1.5.0 動作確認チェックリスト

実装完了後、本番投入前に必ず確認する項目。

### v1.4.x 段階の確認

- [ ] CustomUser 切替後、既存テスト全件 PASS
- [ ] 既存 superuser が Admin にアクセスできる
- [ ] `Contact.created_by` 等の FK が `accounts.CustomUser` を指している

### 認証・LDAP 連携

- [ ] `AUTH_BACKEND=local` でローカルユーザがログインできる
- [ ] `AUTH_BACKEND=ldap` で LDAP ユーザがログインできる
- [ ] `AUTH_BACKEND=ldap` でローカル管理者（superuser）もログインできる
- [ ] `AUTH_BACKEND=both` で両方のユーザがログインできる
- [ ] LDAP 認証成功時、新規 User が `auth_source='ldap'` / `role=None` / `is_active=True` で生成される
- [ ] LDAP ユーザがローカル ModelBackend 経由で空パスワードログインできない（`set_unusable_password()` 検証）
- [ ] LDAP 認証成功時、既存 User の `is_active` が LDAP disabled でなければ上書きされない
- [ ] LDAP 認証成功時、既存 User の `is_active` が LDAP disabled なら False になる
- [ ] LDAP の `mail` 属性が変わっても User の `email` だけ更新される（紐付け不変）
- [ ] LDAP / ローカルの同 username 衝突時、LDAP 側が作成されずエラーログが残る
- [ ] LDAP キャッシュ ON/OFF が `.env` の `LDAP_CACHE_ENABLED` で切り替わる
- [ ] **`populate_user` シグナルが正しく発火し、`sync_ldap_user` が呼ばれる**
- [ ] **LDAP 由来の Department / LdapGroup が正しく upsert される**

### Role / Permission

- [ ] **初期データ Migration 後、`Group.objects.filter(name='person_admin').exists()` が True**
- [ ] **初期データ Migration 後、`Role.objects.filter(code='admin').exists()` が True**
- [ ] **`admin` Role の `default_groups` に `person_admin` / `user_admin` が含まれる**
- [ ] Admin で Role を変更すると `default_groups` が自動反映される
- [ ] Admin で Role を変更せず保存しただけでは Group が再反映されない
- [ ] Role を None に変更すると Groups が空になる
- [ ] `user.role = X; user.save()` 直呼びでは Group が同期されない
- [ ] `persons.undo_merge` Permission が `persons` アプリの Meta に定義されている
- [ ] `admin` Role 付与の superuser が `user.has_perm('persons.undo_merge')` で True を返す
- [ ] **`cards.create_card` Permission が `cards` アプリの Meta に定義されている**（v1.5 で追加）
- [ ] **初期データ Migration 後、`Group.objects.filter(name='card_admin').exists()` が True**（v1.5 で追加）
- [ ] **`admin` Role の `default_groups` に `card_admin` が含まれる**（v1.5 で追加）

### Admin 編集ガード

- [ ] `auth_source='ldap'` の Group / Department / LdapGroup を Admin で開いたとき読み取り専用
- [ ] Admin の URL 直叩きでも user_permissions を変更できない

### User-Person 紐付け

- [ ] 新規 LDAP User は `person=None` で生成される
- [ ] **ホーム画面で email マッチ候補があると `app-alert` でアラートが表示される**
- [ ] ORM 完結クエリで Python 側ループが発生しない
- [ ] 複数候補時は「先に Person をマージ」と促される
- [ ] 紐付け済 User がもう一度ログインしてもアラートが出ない
- [ ] **アラートから「紐付ける」を押すと `link_user_person` View に POST**
- [ ] 既に紐付けある User が別 Person に紐付け試行 → ValidationError
- [ ] 既に User がある Person に別 User を紐付け試行 → ValidationError
- [ ] `person.linked_user` プロパティが None を返す（未紐付け時、例外を投げない）

### マージへの権限拡張

- [ ] 両方 User 紐付き Person のマージは ValidationError（atomic 開始前）
- [ ] 片方 User 紐付きの場合、紐付き User 本人がマージできる
- [ ] 退職者の Person は紐付き User 本人がマージできない
- [ ] 退職者の Person を `managed_by` の現職者がマージできる
- [ ] survive=Q（User無し）で P（User紐付き）を merged にするマージで、User の紐付けが Q に張り替わる
- [ ] User 紐付け引き継ぎは `transfer_contacts_to` の後、`mark_as_merged` の前で実行されている
- [ ] DuplicateCandidate 生成で「両方 User 紐付き」ペアが候補に上がらない
- [ ] Mark_as_Different_Person で権限がない operator は PermissionDenied
- [ ] Execute_Merge_Undo で `persons.undo_merge` Permission がないユーザは PermissionDenied
- [ ] `Execute_Merge_Only` の `transaction.atomic()` が冒頭から包む構造を維持している
- [ ] 権限不足時は `transaction.atomic()` を開かずに PermissionDenied になる
- [ ] **View 層と Service 層の両方で権限チェックが効いている（API 直叩きでも Service 層が止める）**

### 退職処理

- [ ] **Admin actions「退職処理（後継者選択あり）」が User 一覧で選択可能**
- [ ] **Admin actions のインターメディエイト画面で後継者を選択できる**
- [ ] **Admin actions で `retire_user()` が実行され、`persons_managed` / `contacts_managed` が successor に移る**
- [ ] **専用 View `/accounts/users/<id>/retire/` で個別退職処理ができる**
- [ ] 退職処理後、`user.is_active=False` になる
- [ ] 退職処理後の LDAP 同期で `is_active` が True に戻らない
- [ ] 退職者の `User.person` 紐付けは維持される
- [ ] `retire_user(X, X)` は ValidationError
- [ ] 後継者が非アクティブだと ValidationError

### 監査ログ

- [ ] 紐付け / 解除 / マージ / 退職処理が `ActionLog` に記録される
- [ ] action 値は `ActionLogAction` 定数経由で文字列がセットされる
- [ ] 既存 'merged' / 'undone' / 'different_person' のログと並列に保存される

### パフォーマンス（予防）

- [ ] テンプレートで `obj.can_be_X_by(user)` を多用しているリスト画面がない
- [ ] `Department.descendants()` を本番投入していない
- [ ] Admin の `CustomUserAdmin` は `list_select_related=('role', 'department')` を設定
- [ ] ホーム画面アラートのクエリが 1 回で完結（Python 側ループなし）

### マイグレーション

- [ ] v1.5.0 マイグレーションが完走する
- [ ] マイグレーションの `dependencies` に `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` が含まれる
- [ ] **`create_initial_roles_and_groups` Migration が完走する**
- [ ] **`assign_admin_role_to_superuser` Migration で既存 superuser に admin Role が付く**
- [ ] 既存 User 全件が `auth_source='local'` でセットされる
- [ ] `Person.managed_by` / `Contact.managed_by` カラムが追加される
- [ ] 既存 Person / Contact レコードは `managed_by=NULL` で始まる

---

以上が v1.1 改訂版です。**コード君が実装で迷う箇所**を解消し、実装場所・UI 実装方針・初期データ Migration まで明示しました。

### コード君（神戸君）への発注時の注意点（v1.1 改訂版）

1. **§13.2 のコード例を完全になぞる**こと（atomic 構造、手順順序、User 紐付け引き継ぎ位置）
2. **`person.user` への直接アクセスは禁止**、必ず `person.linked_user` 経由
3. **`ActionLog.record()` の action は `ActionLogAction.*` 定数経由**で渡す
4. **`_can_undo_merge` は Permission ベース**（`user.has_perm('persons.undo_merge')`）
5. **マイグレーションの `dependencies` に `swappable_dependency` を必ず含める**
6. **退職時の User.person 紐付けは維持**（各機能で `is_active` フィルタ）
7. **LDAP 同期は `accounts/ldap_sync.py` + `accounts/signals.py` に配置**
8. **初期データ Migration `create_initial_roles_and_groups` を必ず作る**
9. **退職処理 UI は Admin actions + 専用 View 両方実装**
10. **View 層と Service 層の二重防衛を徹底**（§13.7 参照）

実装後は付録 C のチェックリストで **v1.4.2 構造との整合性**を必ず検証してください。

---

## 付録 D 未決論点メモ（v1.3 追記）

v1.2 で `LdapGroup` リネームが確定したが、その後の議論で **`LdapGroup` の存在意義そのもの** と **v1.6+ AccessList での扱い** について結論保留となった論点を記録する。本付録は仕様書本体の設計判断ではなく、**将来の設計判断材料**として残すもの。

### D.1 論点の発端

たんたんの問いかけ:

> 「LDAP の memberOf も、FreeGroup2 ではあんまりいらんような気がするけど、どうなんだろうね。どういうときに使われるの？」

この問いに対して結論保留となった。v1.5.0 仕様書では `LdapGroup` モデルと `memberOf` 同期コード（`sync_ldap_groups`）の実装を前提としているが、その必要性自体が再検討対象になっている。

### D.2 確定している周辺方針

未決論点を議論する前提として、以下は確定済み。

| 確定事項 | 内容 |
| --- | --- |
| AD グループ概念の取り込み範囲 | `groupType`（Security/Distribution）と `groupScope`（Local/Global/Universal）は **FreeGroup2 に取り込まない**。LDAP 同期の境界で全部捨てて、DN と表示名だけ拾う |
| v1.6+ AccessList の主体 | **`UserGroup` のみ**。LDAP 由来の `LdapGroup` を直接 AccessList のターゲットにするかどうかは運用判断（DB 構造では強制しない） |
| 権限管理の思想 | **独立型**（FreeGroup2 内で完結）。LDAP グループでロールを自動付与する AD 依存型は採用しない |

### D.3 論点一覧（v1.4 で論点 A 確定）

| 論点 | 状態 | 説明 |
| --- | --- | --- |
| **A**: `memberOf` 受け皿そのものが必要か | **✅ 確定: 実装する**（v1.4） | **製品要件として AD 対応が必要**（自社運用は AD なしだが顧客に AD 運用が多い）。`AUTH_BACKEND` 環境変数で ON/OFF 制御可能なため、自社運用にも邪魔にならない。`LdapGroup` モデル + `sync_ldap_groups` 関数を v1.5.0 で実装 |
| **B**: 権限管理思想と memberOf の整合 | 未決 | 「独立型」を貫くなら memberOf は業務に流さない（§2 方針通り）。ただし v1.6 メールマーケティングで「営業部全員に配信」など memberOf 由来グループを使う可能性は残る |
| **C**: 導入規模との整合性 | 未決 | 大企業（数千人、AD 運用）では memberOf 同期メリット大。中小企業（数十〜数百人、`AUTH_BACKEND=local`）では手動管理で十分。論点 A 確定により、両方サポート（同期コードは ON/OFF 切替可能） |
| **D**: v1.6 メールマーケティングとの関係 | 未決 | メール配信先指定で memberOf 由来グループを使うかどうか。使うなら LdapGroup を読む経路が増える |
| **E**: 実装と利用は分離可能 | 参考 | 「LDAP 同期コードは書くが、業務（AccessList・メール）では使わない」もアリ。「コードを書く」と「データを使う」は別問題（v1.4 では「書く」が確定、「業務での使い方」は未決） |
| **F**: v1.6+ で LdapGroup と UserGroup の関係 | 未決 | (1) 完全独立 / (2) UserGroup が LdapGroup を継承（Django multi-table inheritance）/ (3) UserGroup が LdapGroup を M2M で包含、のどれを採用するか |
| **G**: v1.6+ AccessList のターゲット型 | 未決 | (a) LdapGroup と UserGroup の両方を polymorphic で受け付ける / (b) 抽象基底 BaseGroup を作って参照 / (c) UserGroup のみ（LdapGroup は UserGroup 経由）|

### D.4 v1.5.0 仕様書での扱い（v1.4 で確定）

論点 A が「**実装する**」で確定したため、v1.5.0 仕様書は **確定方針として実装する** 立て付けになる。

| 項目 | v1.5.0 仕様書の方針 | 根拠 |
| --- | --- | --- |
| `LdapGroup` モデル | **実装する（確定）** | 論点 A 確定（v1.4） |
| `sync_ldap_groups` 関数 | **実装する（確定）** | 論点 A 確定（v1.4） |
| `LdapGroup` の UI | なし（v1.5.0 では業務利用なし） | v1.6+ で AccessList と合わせて判断（論点 F / G） |
| `LdapGroup.auth_source` | `ldap` 固定 | 1 テーブル両用にしない設計（v1.2 で確定） |
| `AUTH_BACKEND=local` 時の挙動 | `sync_ldap_groups` は呼ばれない、`LdapGroup` テーブルは空のまま | §5.1 / §5.4 |

### D.5 一般的に LDAP memberOf が使われる場面（議論メモ）

| パターン | 内容 | FreeGroup2 での採用 |
| --- | --- | --- |
| SSO 時のロール自動付与 | 「AD の "サーバ管理者" グループ所属者にアプリ管理者ロールを付与」 | **現時点では不採用**（§2 で `auth.Group` への流し込みを意図的に避けている。ただし v1.6+ のメールマーケティング・AccessList の運用次第で再評価の余地あり） |
| 通知・配信先の動的決定 | 「営業部全員にメール送信」 | v1.6 メールマーケティングで採用の可能性あり（論点 D） |
| アクセス制御のグループ単位指定 | 「営業 1 課全員に閲覧権限」 | v1.6+ AccessList で採用の可能性あり（論点 F / G） |

### D.6 v1.6+ で判断するための観点

- ネットワーク東海（社内 AD 運用）と中小企業向け販売（AD なし）の **どちらを主想定にするか**
- v1.6 メールマーケティングが memberOf 由来グループを使うかどうかが固まるタイミング
- AccessList 運用イメージ（管理者は FreeGroup2 管理者のみか、AD 管理者連携か）が固まるタイミング

### D.7 仕様書本体への影響

**仕様書本体に変更なし**。v1.4 で論点 A が「実装する」で確定したため、v1.5.0 仕様書本体（§4.5 `LdapGroup` モデル、§5.4 `sync_ldap_groups` 関数）はそのまま実装方針として有効。

残る未決論点（B / D / F / G）は v1.6+ 設計時に詰める。これらは v1.5.0 実装には影響しない（LdapGroup を業務でどう使うかの議論であって、v1.5.0 では LdapGroup は受け皿としてのみ存在する）。