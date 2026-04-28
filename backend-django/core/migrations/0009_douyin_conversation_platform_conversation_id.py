from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_douyin_rule_account_optional"),
    ]

    operations = [
        migrations.AddField(
            model_name="douyinconversation",
            name="platform_conversation_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="抖音平台侧 conversation_id（HTTP 协议收发使用）",
                max_length=128,
                null=True,
            ),
        ),
    ]
