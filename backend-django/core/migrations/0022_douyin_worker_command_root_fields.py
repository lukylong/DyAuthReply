# 补全 DouyinWorkerCommand 的 RootModel 字段（0021 遗漏 sys_creator 等）

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_douyin_worker_command'),
    ]

    operations = [
    ]
