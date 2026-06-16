from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_grant_assign_role_to_user'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='customuser',
            options={'permissions': [
                ('link_user_to_person', 'User と Person の紐付けを管理できる'),
                ('retire_user', 'ユーザを退職処理できる'),
                ('assign_role_to_user', 'ユーザにロールを割り当てられる'),
                ('manage_role', '業務ロールを管理できる'),
            ]},
        ),
    ]
