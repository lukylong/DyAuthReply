# Generated manually for DyAuthReply client (SQLite command queue)

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_add_douyin_worker_monitor_menu'),
    ]

    operations = [
        migrations.CreateModel(
            name='DouyinWorkerCommand',
            fields=[
                (
                    'id',
                    models.CharField(
                        default=uuid.uuid4,
                        editable=False,
                        help_text='主键ID',
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'sys_creator',
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        help_text='创建人',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='%(app_label)s_%(class)s_created',
                        to='core.user',
                    ),
                ),
                ('sys_create_datetime', models.DateTimeField(auto_now_add=True, db_index=True, help_text='创建时间')),
                (
                    'sys_modifier',
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        help_text='修改人',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='%(app_label)s_%(class)s_modified',
                        to='core.user',
                    ),
                ),
                ('sys_update_datetime', models.DateTimeField(auto_now=True, db_index=True, help_text='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False, help_text='是否删除（软删除标识）')),
                ('sort', models.IntegerField(db_index=True, default=0, help_text='排序（数字越大越靠前）')),
                ('channel', models.CharField(db_index=True, help_text='同 Redis 频道名', max_length=255)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('consumed_at', models.DateTimeField(blank=True, db_index=True, null=True)),
            ],
            options={
                'db_table': 'core_douyin_worker_command',
                'ordering': ['is_deleted', '-sort', '-sys_create_datetime'],
                'indexes': [
                    models.Index(fields=['consumed_at', 'sys_create_datetime'], name='core_douyin_consume_idx'),
                ],
            },
        ),
    ]
