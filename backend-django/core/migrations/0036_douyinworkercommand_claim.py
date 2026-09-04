from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0035_douyin_conversation_send_context'),
    ]

    operations = [
        migrations.AddField(
            model_name='douyinworkercommand',
            name='claim_owner',
            field=models.CharField(blank=True, db_index=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='douyinworkercommand',
            name='claimed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
