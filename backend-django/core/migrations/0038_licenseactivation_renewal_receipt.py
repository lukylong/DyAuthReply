from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0037_agent_account_ownership')]
    operations = [migrations.AddField(
        model_name='licenseactivation', name='renewal_receipt',
        field=models.JSONField(default=dict, blank=True, editable=False, help_text='服务端加密续签幂等回执'),
    )]
