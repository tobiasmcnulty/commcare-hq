# Generated by Django 1.11.14 on 2018-09-04 12:05
from corehq.sql_db.operations import RawSQLMigration
from django.db import migrations

from custom.icds_reports.const import SQL_TEMPLATES_ROOT

migrator = RawSQLMigration((SQL_TEMPLATES_ROOT,))


class Migration(migrations.Migration):

    dependencies = [
        ('icds_reports', '0063_aggregatebirthpreparednesforms_anc_abnormalities'),
    ]

    operations = [
        migrator.get_migration('update_tables27.sql'),
    ]
