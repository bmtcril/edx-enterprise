# Generated by Django 4.2.10 on 2024-06-14 08:48

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('enterprise', '0214_alter_enterprisecustomer_learner_portal_sidebar_content_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='enterprisecustomer',
            name='career_engagement_network_message',
        ),
        migrations.RemoveField(
            model_name='enterprisecustomer',
            name='enable_career_engagement_network_on_learner_portal',
        ),
        migrations.RemoveField(
            model_name='historicalenterprisecustomer',
            name='career_engagement_network_message',
        ),
        migrations.RemoveField(
            model_name='historicalenterprisecustomer',
            name='enable_career_engagement_network_on_learner_portal',
        ),
    ]