from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_douyin_worker_command_root_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='douyinconversation',
            name='peer_unique_id',
            field=models.CharField(
                blank=True,
                help_text='对方抖音号（unique_id）',
                max_length=64,
                null=True,
            ),
        ),
    ]
