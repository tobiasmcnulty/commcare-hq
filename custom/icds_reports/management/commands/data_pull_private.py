import csv
import datetime

from dateutil import parser
from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand
from django.db import connections

from corehq.form_processor.interfaces.dbaccessors import CaseAccessors
from custom.icds_reports.models import AwcLocation
from custom.icds_reports.utils.connections import get_icds_ucr_citus_db_alias

db_alias = get_icds_ucr_citus_db_alias()

STATE_ID = '3518687a1a6e4b299dedfef967f29c0c'


def _run_custom_sql_script(command):
    with connections[db_alias].cursor() as cursor:
        cursor.execute(command)
        return cursor.fetchall()


class Command(BaseCommand):

    def get_district_ids_dict(self):
        filters = {'state_id': STATE_ID, 'aggregation_level': 2}
        locations = AwcLocation.objects.filter(**filters).values('district_id', 'district_name')
        district_names = {}
        for loc in locations:
            district_names[loc['district_id']] = loc['district_name']
        return district_names

    def handle(self, **options):
        query = """
            SELECT "awc_location_months"."district_id", "child_health_monthly"."child_person_case_id" FROM "public"."child_health_monthly" "child_health_monthly"
            LEFT JOIN "public"."awc_location_months" "awc_location_months" ON (
                ("awc_location_months"."month" = "child_health_monthly"."month") AND
                ("awc_location_months"."awc_id" = "child_health_monthly"."awc_id")
            ) WHERE "child_health_monthly".month='{month}' AND "awc_location_months".state_id='{state_id}' AND "child_health_monthly".pse_eligible=1;
        """

        months = [datetime.date(2020, 7, 1), datetime.date(2020, 8, 1)]
        case_accessor = CaseAccessors('icds-cas')
        get_district_ids = self.get_district_ids_dict()
        for month in months:
            excel_data = [['district', 'count']]
            person_case_ids = {}
            for district_id in get_district_ids.keys():
                person_case_ids[district_id] = []

            for item in _run_custom_sql_script(query.format(month=month, state_id=STATE_ID)):
                if item[0] in get_district_ids.keys():
                    person_case_ids[item[0]].append(item[1])

            for key, val in person_case_ids:
                count_private_school_going = 0
                district = get_district_ids[key]
                for case in case_accessor.get_cases(val):
                    date_last_private_admit = case.get_case_property('date_last_private_admit')
                    date_return_private = case.get_case_property('date_return_private')
                    next_month = month + relativedelta(months=1)
                    if date_last_private_admit is not None and parser.parse(date_last_private_admit) < next_month:
                        if (not date_return_private) or parser.parse(date_return_private) >= next_month:
                            count_private_school_going += 1
                excel_data.append([district, count_private_school_going])
            fout = open(f'/home/cchq/private_students_{month}.csv', 'w')
            writer = csv.writer(fout)
            writer.writerows(excel_data)
