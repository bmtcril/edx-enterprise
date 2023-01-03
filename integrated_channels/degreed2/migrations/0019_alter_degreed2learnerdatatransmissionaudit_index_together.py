# Generated by Django 3.2.15 on 2023-01-03 14:46

from django.db import connection, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('degreed2', '0018_move_and_recrete_completed_timestamp'),
    ]

    db_engine = connection.settings_dict['ENGINE']
    if 'sqlite3' in db_engine:
        operations = [
            migrations.AlterIndexTogether(
                name='degreed2learnerdatatransmissionaudit',
                index_together={('enterprise_customer_uuid', 'plugin_configuration_id')},
            ),
        ]
    else:
        operations = [
            migrations.SeparateDatabaseAndState(
                state_operations=[
                    migrations.AlterIndexTogether(
                        name='degreed2learnerdatatransmissionaudit',
                        index_together={('enterprise_customer_uuid', 'plugin_configuration_id')},
                    ),
                ],
                database_operations=[
                    migrations.RunSQL(sql="""
                        CREATE INDEX degreed2_dldta_85936b55_idx
                        ON degreed2_degreed2learnerdatatransmissionaudit (enterprise_customer_uuid, plugin_configuration_id)
                        ALGORITHM=INPLACE LOCK=NONE
                    """, reverse_sql="""
                        DROP INDEX degreed2_dldta_85936b55_idx
                        ON degreed2_degreed2learnerdatatransmissionaudit
                    """),
                ]
            ),
        ]
