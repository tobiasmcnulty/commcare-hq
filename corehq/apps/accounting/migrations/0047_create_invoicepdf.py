# -*- coding: utf-8 -*-
# Generated by Django 1.11.27 on 2020-03-10 19:11
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0046_new_plans'),
    ]

    operations = [
        migrations.CreateModel(
            name='SQLInvoicePdf',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('invoice_id', models.PositiveIntegerField(null=True)),
                ('date_created', models.DateTimeField(null=True)),
                ('is_wire', models.BooleanField(default=False)),
                ('is_customer', models.BooleanField(default=False)),
                ('blob_key', models.CharField(max_length=255, null=True)),
                ('couch_id', models.CharField(db_index=True, max_length=126, null=True)),
            ],
            options={
                'db_table': 'accounting_invoicepdf',
            },
        ),
    ]
