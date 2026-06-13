# 将 sec_uid='' 归一为 NULL，避免 unique 约束冲突

from django.db import migrations


def empty_sec_uid_to_null(apps, schema_editor):
    DouyinAccount = apps.get_model('core', 'DouyinAccount')
    DouyinAccount.objects.filter(sec_uid='').update(sec_uid=None)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_add_message_reply_menu'),
    ]

    operations = [
        migrations.RunPython(empty_sec_uid_to_null, migrations.RunPython.noop),
    ]
