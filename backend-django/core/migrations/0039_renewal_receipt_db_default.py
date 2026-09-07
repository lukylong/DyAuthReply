from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('core', '0038_licenseactivation_renewal_receipt')]
    operations = [migrations.AlterField(
        model_name='licenseactivation', name='renewal_receipt',
        field=models.JSONField(default=dict, db_default={}, blank=True, editable=False, help_text='服务端加密续签幂等回执'),
    )]
