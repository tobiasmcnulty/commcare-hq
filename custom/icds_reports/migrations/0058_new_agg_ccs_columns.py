# Generated by Django 1.11.14 on 2018-08-14 07:42
from corehq.sql_db.operations import RawSQLMigration
from django.db import migrations

from custom.icds_reports.const import SQL_TEMPLATES_ROOT

migrator = RawSQLMigration((SQL_TEMPLATES_ROOT,))


class Migration(migrations.Migration):

    dependencies = [
        ('icds_reports', '0057_aggregateccsrecordpostnatalcareforms_is_ebf'),
    ]

    operations = [
        migrator.get_migration('update_tables25.sql'),
    ]
