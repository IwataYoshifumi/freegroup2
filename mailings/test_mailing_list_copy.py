from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from mailings.models import MailingList, MailingListMember
from persons.models import Person
from contacts.models import Contact
from django.contrib.auth import get_user_model

CustomUser = get_user_model()

class MailingListCopyTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="testuser", password="password", email="test@example.com"
        )
        # 権限付与
        from django.contrib.auth.models import Permission
        perm_add = Permission.objects.get(codename="add_mailinglist")
        perm_view = Permission.objects.get(codename="view_mailinglist")
        self.user.user_permissions.add(perm_add, perm_view)
        
        # テストデータの作成
        self.original_list = MailingList.objects.create(
            name="Original List",
            description="Test Description",
            created_by=self.user,
            extraction_snapshot={"foo": "bar"},
            members_frozen_at=timezone.now(),
            is_archived=False,
        )
        
        # メンバー作成
        self.person1 = Person.objects.create()
        self.contact1 = Contact.objects.create(email="test1@example.com", person=self.person1)
        self.person1.primary_contact = self.contact1
        self.person1.save()
        
        self.person2 = Person.objects.create()
        self.contact2 = Contact.objects.create(email="test2@example.com", person=self.person2)
        self.person2.primary_contact = self.contact2
        self.person2.save()
        
        self.member1 = MailingListMember.objects.create(
            mailing_list=self.original_list,
            person=self.person1,
            added_by=self.user,
        )
        self.member2 = MailingListMember.objects.create(
            mailing_list=self.original_list,
            person=self.person2,
            added_by=self.user,
        )

        self.copy_url = reverse("mailings:mailing_list_copy", args=[self.original_list.pk])

    def test_copy_mailing_list_post(self):
        self.client.login(username="testuser", password="password")
        
        # POSTリクエストでコピー実行
        response = self.client.post(self.copy_url)
        
        # リストが1つ増えていることを確認
        self.assertEqual(MailingList.objects.count(), 2)
        new_list = MailingList.objects.exclude(pk=self.original_list.pk).first()
        
        # 複製されたデータの検証
        self.assertEqual(new_list.name, "Original List のコピー")
        self.assertEqual(new_list.description, "Test Description")
        self.assertEqual(new_list.created_by, self.user)
        self.assertEqual(new_list.extraction_snapshot, {"foo": "bar"})
        self.assertIsNone(new_list.members_frozen_at)
        
        # メンバーの検証
        new_members = new_list.members.all()
        self.assertEqual(new_members.count(), 2)
        person_ids = set(new_members.values_list("person_id", flat=True))
        self.assertEqual(person_ids, {self.person1.id, self.person2.id})
        
        for m in new_members:
            self.assertEqual(m.added_by, self.user)
            
        # 詳細画面へリダイレクトされること
        self.assertRedirects(response, reverse("mailings:mailing_list_detail", args=[new_list.pk]))

    def test_copy_mailing_list_get_not_allowed(self):
        self.client.login(username="testuser", password="password")
        
        response = self.client.get(self.copy_url)
        self.assertEqual(response.status_code, 405)  # Method Not Allowed

    def test_copy_archived_list(self):
        self.client.login(username="testuser", password="password")
        self.original_list.is_archived = True
        self.original_list.save()
        
        response = self.client.post(self.copy_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MailingList.objects.count(), 2)
