# 补全 DouyinWorkerCommand 的 RootModel 字段（0021 遗漏 sys_creator 等）

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_douyin_worker_command'),
    ]

    operations = [
        migrations.AddField(
            model_name='douyinworkercommand',
            name='sys_creator',
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text='创建人',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='%(app_label)s_%(class)s_created',
                to='core.user',
            ),
        ),
        migrations.AddField(
            model_name='douyinworkercommand',
            name='sys_modifier',
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text='修改人',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='%(app_label)s_%(class)s_modified',
                to='core.user',
            ),
        ),
        migrations.AddField(
            model_name='douyinworkercommand',
            name='is_deleted',
            field=models.BooleanField(db_index=True, default=False, help_text='是否删除（软删除标识）'),
        ),
        migrations.AddField(
            model_name='douyinworkercommand',
            name='sort',
            field=models.IntegerField(db_index=True, default=0, help_text='排序（数字越大越靠前）'),
        ),
        migrations.AlterField(
            model_name='douyinworkercommand',
            name='id',
            field=models.CharField(
                default=uuid.uuid4,
                editable=False,
                help_text='主键ID',
                max_length=36,
                primary_key=True,
                serialize=False,
            ),
        ),
    ]
