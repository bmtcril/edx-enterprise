# Generated by Django 3.2.23 on 2024-02-27 22:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('enterprise', '0200_enterprisegroup_enterprisegroupmembership_historicalenterprisegroup_historicalenterprisegroupmembers'),
    ]

    operations = [
        migrations.AddField(
            model_name='enterprisegroup',
            name='is_removed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='enterprisegroupmembership',
            name='is_removed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='historicalenterprisegroup',
            name='is_removed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='historicalenterprisegroupmembership',
            name='is_removed',
            field=models.BooleanField(default=False),
        ),
    ]