"""DouyinRule.account 改为可空，支持全局规则（对所有账号生效）。

设计：
- account_id IS NULL  → 全局规则，对该用户名下所有抖音账号生效（兜底/默认规则）
- account_id IS NOT NULL → 账号专属规则
- 匹配时账号专属规则与全局规则一起按 priority 降序匹配；同 priority 时账号专属优先
  （由 worker._load_rules_for_account 的 ORDER BY 控制）
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_douyin_account_auto_reply_enabled'),
    ]

    operations = [
        migrations.AlterField(
            model_name='douyinrule',
            name='account',
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                db_index=True,
                help_text='规则所属的抖音账号；为空表示全局规则，对所有账号生效',
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='rules',
                to='core.douyinaccount',
            ),
        ),
    ]
