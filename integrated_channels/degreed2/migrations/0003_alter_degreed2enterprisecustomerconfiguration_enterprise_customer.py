# Generated by Django 3.2.8 on 2021-12-20 17:24

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('enterprise', '0151_add_is_active_to_invite_key'),
        ('degreed2', '0002_auto_20211101_2021'),
    ]

    operations = [
        migrations.AlterField(
            model_name='degreed2enterprisecustomerconfiguration',
            name='enterprise_customer',
            field=models.ForeignKey(help_text='Enterprise Customer associated with the configuration.', on_delete=django.db.models.deletion.CASCADE, to='enterprise.enterprisecustomer'),
        ),
    ]