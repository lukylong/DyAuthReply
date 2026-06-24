from django.db import migrations, models


def forwards_copy_account(apps, schema_editor):
    """将旧单账号 FK (account_id) 迁移到 account_ids 列表。"""
    DouyinRule = apps.get_model('core', 'DouyinRule')
    for rule in DouyinRule.objects.all().iterator():
        acc_id = getattr(rule, 'account_id', None)
        if acc_id and not rule.account_ids:
            rule.account_ids = [str(acc_id)]
            rule.save(update_fields=['account_ids'])


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_add_client_announcement_menu'),
    ]

    operations = [
        migrations.AddField(
            model_name='douyinrule',
            name='account_ids',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='显式绑定的账号 ID 列表；为空表示全局规则，对所有未绑定账号生效。一个账号只能属于一条规则',
            ),
        ),
        migrations.RunPython(forwards_copy_account, backwards_noop),
    ]
