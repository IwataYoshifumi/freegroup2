"""dev_create_mailing_test_data 管理コマンド（開発ツール）。

メール配信、配信レポート、配信拒否リスト、リスト作成/編集の各UI確認用に、
MailingList、MailingListMember、Campaign、EmailTemplate、DeliveryHistory、
TrackingLink、ClickLog、SuppressedEmail の一括モックデータを生成する。

設計概要：
- 対象 Person は既存の active なもの（Person.Status.ACTIVE）のみを使用。
  Person が1件も存在しない場合は処理を行わずに終了する。
- メール配信仕様書（rev21）の設計方針を厳守：
  - 本文はプレーンテキストのみ。
  - Campaign と EmailTemplate は 1:1（OneToOne）。
  - Campaign は mailing_list を1つ参照（tag_condition 廃止）。
  - リストの凍結タイミングは「送信開始時」。sending/done のキャンペーンが参照するリストのみ
    members_frozen_at がセットされ、draft/scheduled のリストは NULL。
  - 配信時点のスナップショット項目（to_email / organization_at_send / full_name_at_send /
    salutation_name_at_send）を送信時点の primary_contact の値で埋める。
- --reset オプションで、このコマンドが生成したメール配信系データを初期化してから再生成する。

専用ユーザー：
- `test_data_user`（または --user で指定されたユーザー）が Django Admin 等で事前作成されている前提。
- 存在しない場合はエラー終了。
"""

from __future__ import annotations

import random
import secrets
from datetime import timedelta
from typing import Dict, List

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from contacts.models import Contact
from persons.models import Person
from mailings.models import (
    Campaign,
    EmailTemplate,
    MailingList,
    MailingListMember,
    DeliveryHistory,
    TrackingLink,
    ClickLog,
    SuppressedEmail,
    SoftBounceCounter,
)

User = get_user_model()
DEFAULT_USER = "test_data_user"
DEFAULT_SEED = 42


class Command(BaseCommand):
    help = (
        "メール配信・配信レポート・リスト管理UI確認用の"
        "MailingList, Campaign, EmailTemplate, DeliveryHistory などのモックデータを生成します。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="このコマンドが生成したメール配信系データを削除してから生成する",
        )
        parser.add_argument(
            "--user",
            default=DEFAULT_USER,
            help=f"生成を行う管理ユーザー名（既定 {DEFAULT_USER}）",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=DEFAULT_SEED,
            help="乱数再現用シード（既定 42）",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="詳細なログを出力する",
        )

    def handle(self, *args, **options):
        username = options["user"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(
                f"ユーザー '{username}' が存在しません。"
                f"Django Admin で事前作成してから実行してください。"
                f"本コマンドはユーザーを作成しません。"
            )

        # active な Person を取得（存在しなければ終了）
        active_persons = list(Person.objects.filter(status=Person.Status.ACTIVE))
        if not active_persons:
            self.stdout.write(
                self.style.WARNING("active な Person が存在しません。メール配信データの生成は行われません。")
            )
            return

        rng = random.Random(options["seed"])

        with transaction.atomic():
            if options["reset"]:
                # このコマンドで生成されるメール配信関係データをクリア
                # CASCADE で ClickLog, TrackingLink, DeliveryHistory, MailingListMember 等も自動削除されるが
                # PROTECT 等を考慮して依存順に削除

                # 1. ClickLog を削除
                deleted_clicks, _ = ClickLog.objects.filter(
                    tracking_link__campaign__created_by=user
                ).delete()

                # 2. TrackingLink を削除
                deleted_tracking_links, _ = TrackingLink.objects.filter(
                    campaign__created_by=user
                ).delete()

                # 3. DeliveryHistory を削除
                deleted_delivery, _ = DeliveryHistory.objects.filter(
                    campaign__created_by=user
                ).delete()

                # 4. Campaign を削除（OneToOne PROTECT 回避のため EmailTemplate の前に削除）
                deleted_campaigns, _ = Campaign.objects.filter(created_by=user).delete()

                # 5. EmailTemplate を削除
                deleted_templates, _ = EmailTemplate.objects.filter(created_by=user).delete()

                # 6. MailingListMember を削除
                deleted_members, _ = MailingListMember.objects.filter(added_by=user).delete()

                # 7. MailingList を削除
                deleted_lists, _ = MailingList.objects.filter(created_by=user).delete()

                # 8. SuppressedEmail を削除（Person.primary_contact のメールアドレスに一致するもので作成したため、その範囲で削除）
                person_emails = [
                    p.primary_contact.email
                    for p in active_persons
                    if p.primary_contact and p.primary_contact.email
                ]
                deleted_suppressed, _ = SuppressedEmail.objects.filter(
                    email__in=person_emails
                ).delete()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"リセット完了: 削除されたキャンペーン={deleted_campaigns}, "
                        f"テンプレート={deleted_templates}, "
                        f"配信履歴={deleted_delivery}, "
                        f"リスト={deleted_lists}, "
                        f"拒否リスト={deleted_suppressed}"
                    )
                )

            # --- 1. MailingList ＆ MailingListMember の生成 ---
            # メンバー分け用
            if len(active_persons) < 10:
                raise CommandError(
                    f"active な Person 数が少なすぎます ({len(active_persons)}件)。"
                    f"先に dev_create_test_contact_data で十分なデータを生成してください。"
                )

            # (A) 未凍結リスト（members_frozen_at = NULL）
            unfrozen_list = MailingList.objects.create(
                name="【未凍結】営業ターゲットリスト（新規・未アプローチ）",
                description="新任担当者向けおよび最近名刺交換したアクティブリスト（未凍結のため編集可能）",
                created_by=user,
                members_frozen_at=None,
            )
            # 全体の前半 40% を登録
            unfrozen_members_count = int(len(active_persons) * 0.4)
            for p in active_persons[:unfrozen_members_count]:
                MailingListMember.objects.create(
                    mailing_list=unfrozen_list,
                    person=p,
                    added_by=user
                )

            # (B) 凍結済みリスト（members_frozen_at = 過去時刻）
            frozen_list = MailingList.objects.create(
                name="【凍結】IT・製造業セミナー案内リスト",
                description="2026年6月開催のシステムセミナー向けに抽出した確定配信名簿",
                created_by=user,
                members_frozen_at=timezone.now() - timedelta(days=2),
            )
            # 全体の後半 50% を登録
            frozen_members_count = int(len(active_persons) * 0.5)
            frozen_persons = active_persons[-frozen_members_count:]
            for p in frozen_persons:
                MailingListMember.objects.create(
                    mailing_list=frozen_list,
                    person=p,
                    added_by=user
                )

            if options["verbose"]:
                self.stdout.write(
                    f"リスト作成: {unfrozen_list.name} (メンバー数={unfrozen_members_count})\n"
                    f"リスト作成: {frozen_list.name} (メンバー数={frozen_members_count}, 凍結済み)"
                )

            # --- 2. Campaign ＆ EmailTemplate の生成 ---
            # (A) Campaign 1: 下書き (draft)
            template_draft = EmailTemplate.objects.create(
                name="[下書き用] 夏季休業のお知らせテンプレート",
                subject="{{会社名}} {{役職}} {{氏名}}様 夏季休業期間中の緊急サポート対応について",
                body=(
                    "{{会社名}}\n{{役職}} {{氏名}}様\n\n"
                    "平素は格別のご愛顧を賜り、厚く御礼申し上げます。\n"
                    "株式会社明治テック・サポート窓口でございます。\n\n"
                    "当社は下記日程で夏季休業とさせていただきます。\n\n"
                    "休業期間: 8月13日(木) 〜 8月16日(日)\n\n"
                    "休業期間中の緊急連絡先は以下よりご確認ください。\n"
                    "{% tracked_link %}https://example.com/support/summer-emergency{% endtracked_link %}\n\n"
                    "今後ともよろしくお願い申し上げます。\n\n"
                    "配信停止はこちらから:\n"
                    "{% unsubscribe_link %}配信停止手続き{% endunsubscribe_link %}"
                ),
                created_by=user,
            )
            campaign_draft = Campaign.objects.create(
                name="夏季休業のお知らせ（下書き）",
                template=template_draft,
                mailing_list=unfrozen_list,
                scheduled_at=None,
                status=Campaign.Status.DRAFT,
                sender_mode=Campaign.SenderMode.NEWSLETTER,
                sender_email="support@meiji-tech.co.jp",
                sender_name="明治テックサポート窓口",
                created_by=user,
            )

            # (B) Campaign 2: 予約配信待機中 (scheduled)
            template_scheduled = EmailTemplate.objects.create(
                name="[予約用] 新サービス発表セミナーテンプレート",
                subject="【招待状】{{氏名}}様へ新サービス発表会のご案内",
                body=(
                    "{{氏名}}様\n\n"
                    "いつもお世話になっております。営業部の山田です。\n"
                    "この度、弊社の新ソリューション発表会を開催いたします。\n\n"
                    "詳細および事前登録は以下のリンクよりお願いいたします。\n"
                    "{% tracked_link %}https://example.com/seminar/registration{% endtracked_link %}\n\n"
                    "皆様のご参加を心よりお待ちしております。\n\n"
                    "{% unsubscribe_link %}"
                ),
                created_by=user,
            )
            campaign_scheduled = Campaign.objects.create(
                name="新サービス発表セミナー（予約）",
                template=template_scheduled,
                mailing_list=unfrozen_list,
                scheduled_at=timezone.now() + timedelta(days=3),
                status=Campaign.Status.SCHEDULED,
                sender_mode=Campaign.SenderMode.CREATOR,
                created_by=user,
            )

            # (C) Campaign 3: 配信中 (sending)
            template_sending = EmailTemplate.objects.create(
                name="[配信中用] 規約改定のお知らせテンプレート",
                subject="【重要】{{会社名}}様 サービス利用規約の改定について",
                body=(
                    "{{会社名}}\n{{氏名}}様\n\n"
                    "平素より弊社サービスをご利用いただきありがとうございます。\n"
                    "この度、利用規約を一部改定することとなりました。\n\n"
                    "改定内容および新旧対照表は以下よりご確認ください。\n"
                    "{% tracked_link %}https://example.com/terms/update-2026{% endtracked_link %}\n\n"
                    "{% unsubscribe_link %}"
                ),
                created_by=user,
            )
            campaign_sending = Campaign.objects.create(
                name="利用規約改定のご連絡（配信中）",
                template=template_sending,
                mailing_list=frozen_list,
                scheduled_at=timezone.now() - timedelta(hours=1),
                status=Campaign.Status.SENDING,
                sender_mode=Campaign.SenderMode.NEWSLETTER,
                sender_email="info@meiji-tech.co.jp",
                sender_name="明治テック管理事務局",
                created_by=user,
            )

            # (D) Campaign 4: 配信完了 (done)
            template_done = EmailTemplate.objects.create(
                name="[配信完了用] ニュースレター創刊号テンプレート",
                subject="FreeGroup2 ニュースレター 創刊号の送付",
                body=(
                    "{{氏名}}様\n\n"
                    "いつもお世話になっております。管理人の佐藤です。\n"
                    "ニュースレター創刊号をお届けいたします。\n\n"
                    "今月の注目記事：\n"
                    "{% tracked_link %}https://example.com/news/article-1{% endtracked_link %}\n\n"
                    "製品サポート情報：\n"
                    "{% tracked_link %}https://example.com/support/qa{% endtracked_link %}\n\n"
                    "配信停止はこちらから:\n"
                    "{% unsubscribe_link %}配信停止{% endunsubscribe_link %}"
                ),
                created_by=user,
            )
            campaign_done = Campaign.objects.create(
                name="メルマガニュースレター（配信完了）",
                template=template_done,
                mailing_list=frozen_list,
                scheduled_at=timezone.now() - timedelta(days=2),
                started_at=timezone.now() - timedelta(days=2),
                completed_at=timezone.now() - timedelta(days=2) + timedelta(minutes=45),
                status=Campaign.Status.DONE,
                sender_mode=Campaign.SenderMode.NEWSLETTER,
                sender_email="newsletter@meiji-tech.co.jp",
                sender_name="明治テック広報部",
                total_count=len(frozen_persons),
                created_by=user,
            )

            if options["verbose"]:
                self.stdout.write(
                    f"キャンペーン作成: {campaign_draft.name} (下書き)\n"
                    f"キャンペーン作成: {campaign_scheduled.name} (予約中)\n"
                    f"キャンペーン作成: {campaign_sending.name} (配信中)\n"
                    f"キャンペーン作成: {campaign_done.name} (配信完了)"
                )

            # --- 3. DeliveryHistory ＆ トラッキング関連の生成 (done キャンペーン用) ---
            # 凍結リストのメンバーに対して送信結果を割り振る
            # バリエーション：送信成功(sent)=大半、バウンス(bounced)=数件、配信停止(unsubscribed)=1件、送信失敗(failed)=1件
            sent_count = 0
            failed_count = 0

            # 事前にトラッキング対象のURLを準備
            url_news = "https://example.com/news/article-1"
            url_support = "https://example.com/support/qa"

            for i, p in enumerate(frozen_persons):
                # 送信時点のコンタクト情報をスナップショット
                contact = p.primary_contact
                to_email = contact.email if contact else f"person_{p.id}@example.com"
                org_name = contact.organization if contact else ""
                full_name = contact.full_name if contact else ""
                salutation = contact.salutation_name if contact else ""

                # ステータスの決定
                if i == 0:
                    status = DeliveryHistory.Status.BOUNCED
                    err_msg = "550 5.1.1 User Unknown"
                    failed_count += 1
                elif i == 1:
                    status = DeliveryHistory.Status.UNSUBSCRIBED
                    err_msg = ""
                    # 実際に配信停止したことを意味する Unsubscribe レコードを作るわけではないが履歴上の status
                    # (配信前に配信停止フラグ is_unsubscribed=True だったための自動除外など)
                    sent_count += 1
                elif i == 2:
                    status = DeliveryHistory.Status.FAILED
                    err_msg = "421 4.3.0 Connection timeout"
                    failed_count += 1
                else:
                    status = DeliveryHistory.Status.SENT
                    err_msg = ""
                    sent_count += 1

                history = DeliveryHistory.objects.create(
                    campaign=campaign_done,
                    person=p,
                    contact=contact,
                    to_email=to_email,
                    organization_at_send=org_name,
                    full_name_at_send=full_name,
                    salutation_name_at_send=salutation,
                    sent_at=campaign_done.started_at + timedelta(seconds=i * 2),
                    status=status,
                    error_message=err_msg,
                )

                # 送信成功した一部の受信者がリンクをクリックしたログをシミュレート
                if status == DeliveryHistory.Status.SENT:
                    # i の値でクリック行動を変える
                    # 偶数番目は1つ目のリンクをクリック
                    if i % 2 == 0:
                        # 1. TrackingLink 構築
                        link = TrackingLink.objects.create(
                            campaign=campaign_done,
                            person=p,
                            original_url=url_news,
                            token=secrets.token_urlsafe(8)[:20]
                        )
                        # 2. ClickLog 作成 (ボット判定=正常)
                        ClickLog.objects.create(
                            tracking_link=link,
                            clicked_at=history.sent_at + timedelta(minutes=15),
                            ip_masked="192.168.10.0",
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            http_method="GET",
                            is_valid_click=True,
                        )
                        # リンク側の集計値更新
                        link.click_count = 1
                        link.total_access_count = 1
                        link.last_clicked_at = history.sent_at + timedelta(minutes=15)
                        link.save()

                    # 3の倍数番目は2つ目のリンクをクリック
                    if i % 3 == 0:
                        link = TrackingLink.objects.create(
                            campaign=campaign_done,
                            person=p,
                            original_url=url_support,
                            token=secrets.token_urlsafe(8)[:20]
                        )
                        ClickLog.objects.create(
                            tracking_link=link,
                            clicked_at=history.sent_at + timedelta(minutes=25),
                            ip_masked="203.0.113.0",
                            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                            http_method="GET",
                            is_valid_click=True,
                        )
                        # 重複クリック（ボット判定=重複無効）をシミュレート
                        ClickLog.objects.create(
                            tracking_link=link,
                            clicked_at=history.sent_at + timedelta(minutes=26),
                            ip_masked="203.0.113.0",
                            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                            http_method="GET",
                            is_valid_click=False,
                            bot_reason=ClickLog.BotReason.DUPLICATE,
                        )
                        link.click_count = 1  # 有効クリックは1
                        link.total_access_count = 2  # 総アクセスは2
                        link.last_clicked_at = history.sent_at + timedelta(minutes=26)
                        link.save()

            # キャンペーンの実カウント更新
            campaign_done.sent_count = sent_count
            campaign_done.failed_count = failed_count
            campaign_done.save(update_fields=["sent_count", "failed_count"])

            # --- 4. SuppressedEmail (配信拒否リスト) の生成 ---
            # 既存 Person の中から重複しないメアドを3つ集めて登録
            suppressed_count = 0
            seen_emails = set()
            for p in active_persons:
                if suppressed_count >= 3:
                    break
                if not p.primary_contact or not p.primary_contact.email:
                    continue

                email = p.primary_contact.email
                if email in seen_emails:
                    continue
                seen_emails.add(email)

                # バリエーション
                if suppressed_count == 0:
                    source = SuppressedEmail.Source.BOUNCE_HARD
                    reason = "550 5.1.1 Address rejected"
                    note = "テスト配信用ハードバウンスシミュレート"
                elif suppressed_count == 1:
                    source = SuppressedEmail.Source.UNSUBSCRIBE_GENERIC
                    reason = ""
                    note = "代表メール配信停止手続きによる自動登録"
                else:
                    source = SuppressedEmail.Source.MANUAL
                    reason = ""
                    note = "管理者判断による手動登録"

                # 重複回避しながら作成 (UniqueConstraint 衝突回避)
                if not SuppressedEmail.objects.filter(email=email, cancelled_at__isnull=True).exists():
                    SuppressedEmail.objects.create(
                        email=email,
                        source=source,
                        bounce_reason=reason,
                        note=note,
                        cancelled_at=None,
                    )
                    suppressed_count += 1

            # カウント情報出力
            dh_stats = {
                "sent": DeliveryHistory.objects.filter(campaign=campaign_done, status=DeliveryHistory.Status.SENT).count(),
                "bounced": DeliveryHistory.objects.filter(campaign=campaign_done, status=DeliveryHistory.Status.BOUNCED).count(),
                "failed": DeliveryHistory.objects.filter(campaign=campaign_done, status=DeliveryHistory.Status.FAILED).count(),
                "unsubscribed": DeliveryHistory.objects.filter(campaign=campaign_done, status=DeliveryHistory.Status.UNSUBSCRIBED).count(),
            }

            self.stdout.write(
                self.style.SUCCESS(
                    f"生成完了:\n"
                    f"  - MailingList: 未凍結=1, 凍結=1 (計2件)\n"
                    f"  - Campaign (EmailTemplate 同時作成): draft=1, scheduled=1, sending=1, done=1 (計4件)\n"
                    f"  - DeliveryHistory (done 用): 送信成功={dh_stats['sent']}, バウンス={dh_stats['bounced']}, 配信停止={dh_stats['unsubscribed']}, 送信失敗={dh_stats['failed']} (計{sum(dh_stats.values())}件)\n"
                    f"  - TrackingLink={TrackingLink.objects.filter(campaign=campaign_done).count()}件, ClickLog={ClickLog.objects.filter(tracking_link__campaign=campaign_done).count()}件\n"
                    f"  - SuppressedEmail={suppressed_count}件"
                )
            )
