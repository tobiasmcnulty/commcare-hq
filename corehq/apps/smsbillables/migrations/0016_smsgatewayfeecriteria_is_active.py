# -*- coding: utf-8 -*-
# Generated by Django 1.11.10 on 2018-02-26 18:53
from __future__ import absolute_import
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('smsbillables', '0015_bootstrap_icds_rates'),
    ]

    operations = [
        migrations.AddField(
            model_name='smsgatewayfeecriteria',
            name='is_active',
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
