from dateutil.relativedelta import relativedelta
from django.db import connections

from corehq.apps.userreports.util import get_table_name
from corehq.sql_db.connections import get_icds_ucr_citus_db_alias
from custom.icds_reports.const import DASHBOARD_DOMAIN
from custom.icds_reports.utils.aggregation_helpers import get_prev_table, transform_day_to_month


class TempPrevTables(object):

    DROP_QUERY = """
    DROP TABLE IF EXISTS "{prev_table}";
    DROP TABLE IF EXISTS "{prev_local}";
    """

    def drop_temp_tables(self, alias):
        data = {
            'prev_table': get_prev_table(alias),
            'prev_local': f"{alias}_prev_local",
        }
        with connections[get_icds_ucr_citus_db_alias()].cursor() as cursor:
            cursor.execute(self.DROP_QUERY.format(**data))

    def create_temp_tables(self, alias, table, day):
        data = {
            'prev_table': get_prev_table(alias),
            'prev_local': f"{alias}_prev_local",
            'prev_month': day,
            'current_table': table,
            'alias': alias
        }
        with connections[get_icds_ucr_citus_db_alias()].cursor() as cursor:
            cursor.execute(self.CREATE_QUERY.format(**data))

    def make_all_tables(self, day):
        raise NotImplementedError


class TempPrevUCRTables(TempPrevTables):

    CREATE_QUERY = """
    CREATE UNLOGGED TABLE "{prev_table}" (LIKE "{current_table}");
    SELECT create_distributed_table('{prev_table}', 'supervisor_id');
    INSERT INTO "{prev_table}" (SELECT * FROM "{current_table}");
    CREATE INDEX "idx_rationalization_date_{alias}" ON "{prev_table}" USING hash (location_rationalisation_date);
    CREATE UNLOGGED TABLE "{prev_local}" AS (SELECT * FROM "{current_table}" WHERE location_rationalisation_date='{prev_month}');
    UPDATE "{prev_local}" SET supervisor_id = last_supervisor_id, awc_id=last_owner_id;
    DELETE FROM "{prev_table}" WHERE location_rationalisation_date='{prev_month}';
    INSERT INTO "{prev_table}" (SELECT * FROM "{prev_local}");
    """

    def drop_temp_tables(self, alias):
        data = {
            'prev_table': get_prev_table(alias),
            'prev_local': f"{alias}_prev_local",
        }
        with connections[get_icds_ucr_citus_db_alias()].cursor() as cursor:
            cursor.execute(self.DROP_QUERY.format(**data))

    def create_temp_tables(self, table, day):
        alias, table = table
        data = {
            'prev_table': get_prev_table(alias),
            'prev_local': f"{alias}_prev_local",
            'prev_month': day,
            'current_table': table,
            'alias': alias
        }
        with connections[get_icds_ucr_citus_db_alias()].cursor() as cursor:
            cursor.execute(self.CREATE_QUERY.format(**data))

    def make_all_tables(self, day):
        day = transform_day_to_month(day) + relativedelta(months=1)
        table_list = [
            ('static-child_health_cases', get_table_name(DASHBOARD_DOMAIN, 'static-child_health_cases')),
            ('static-ccs_record_cases', get_table_name(DASHBOARD_DOMAIN, 'static-ccs_record_cases')),
            ('static-person_cases_v3', get_table_name(DASHBOARD_DOMAIN, 'static-person_cases_v3')),
            ('static-household_cases', get_table_name(DASHBOARD_DOMAIN, 'static-household_cases')),
        ]
        for table in table_list:
            self.drop_temp_tables(table[0])
            self.create_temp_tables(table, day)


class TempPrevIntermediateTables(TempPrevTables):
    CREATE_QUERY = """
    CREATE UNLOGGED TABLE "{prev_table}" (LIKE "{current_table}");
    SELECT create_distributed_table('{prev_table}', 'supervisor_id');
    INSERT INTO "{prev_table}" (SELECT * FROM "{current_table}" where month='{prev_month}');
    CREATE INDEX "idx_sup_case_{alias}" ON "{prev_table}" USING hash (case_id);
    CREATE INDEX "idx_sup_state_{alias}" ON "{prev_table}" USING hash (state_id);
    CREATE UNLOGGED TABLE "{prev_local}" AS (SELECT * FROM "{current_table}" WHERE case_id in (select case_id from "{ucr_local}"));
    DELETE FROM "{prev_table}" WHERE case_id in (select doc_id from "{ucr_local}");
    UPDATE "{prev_local}" prev SET supervisor_id = last_supervisor_id FROM "{ucr_local}" ucr WHERE prev.case_id=ucr.doc_id;
    INSERT INTO "{prev_table}" (SELECT * FROM "{prev_local}");
    """

    def create_temp_tables(self, table, day):
        alias, table, ucr_alias = table
        data = {
            'prev_table': get_prev_table(alias),
            'prev_local': f"{alias}_prev_local",
            'prev_month': day,
            'current_table': table,
            'alias': alias,
            'ucr_local': f"{ucr_alias}_prev_local"
        }
        with connections[get_icds_ucr_citus_db_alias()].cursor() as cursor:
            cursor.execute(self.CREATE_QUERY.format(**data))

    def make_all_tables(self, day):
        day = transform_day_to_month(day) - relativedelta(months=1)
        table_list = [
            ('postnatal-care-forms-child-health', 'icds_dashboard_child_health_postnatal_forms', 'static-child_health_cases'),
            ('growth-monitoring-forms', 'icds_dashboard_growth_monitoring_forms', 'static-child_health_cases'),
            ('birth-preparedness-forms', 'icds_dashboard_ccs_record_bp_forms', 'static-ccs_record_cases'),
            ('postnatal-care-forms-ccs-record', 'icds_dashboard_ccs_record_postnatal_forms', 'static-ccs_record_cases'),
            ('complementary-forms-ccs-record', 'icds_dashboard_ccs_record_cf_forms', 'static-ccs_record_cases'),
            ('complementary-forms', 'icds_dashboard_comp_feed_form', 'static-child_health_cases')
        ]
        for table in table_list:
            self.drop_temp_tables(table[0])
            self.create_temp_tables(table, day)


class TempInfraTables(TempPrevTables):
    CREATE_QUERY = """
    CREATE UNLOGGED TABLE "{prev_table}" (LIKE "{current_table}");
    INSERT INTO "{prev_table}" (SELECT * FROM "{current_table}" where timeend >= '{six_months_ago}' AND timeend < '{next_month_start}');
    CREATE INDEX "idx_sup_state_{alias}" ON "{prev_table}" USING hash (state_id);
    CREATE UNLOGGED TABLE "{prev_local}" AS (SELECT * FROM "{current_table}" WHERE awc_id in (select doc_id from awc_location_local where aggregation_level=5 and awc_deprecated_at  >= '{prev_month}' AND awc_deprecated_at < '{next_month_start}'));
    DELETE FROM "{prev_table}" WHERE awc_id in (select awc_id from "{prev_local}");
    UPDATE "{prev_local}" prev SET supervisor_id = awc.supervisor_id, awc_id=awc.awc_id FROM (select unnest(string_to_array(awc_deprecates, ',')) as prev_awc_id, doc_id as awc_id, supervisor_id FROM "awc_location_local" awc WHERE awc_deprecated_at  >= '{prev_month}' AND awc_deprecated_at < '{next_month_start}' and aggregation_level=5) awc where prev.awc_id=awc.prev_awc_id;
    INSERT INTO "{prev_table}" (SELECT * FROM "{prev_local}");
    """

    def create_temp_tables(self, table, day):
        next_month_start = day + relativedelta(months=1)
        six_months_ago = day - relativedelta(months=6)
        alias, table = table
        data = {
            'prev_table': get_prev_table(alias),
            'prev_local': f"{alias}_prev_local",
            'prev_month': day,
            'current_table': table,
            'alias': alias,
            'six_months_ago': six_months_ago,
            'next_month_start': next_month_start
        }
        with connections[get_icds_ucr_citus_db_alias()].cursor() as cursor:
            cursor.execute(self.CREATE_QUERY.format(**data))

    def make_all_tables(self, day):
        day = transform_day_to_month(day)
        table_list = [
            ('static-infrastructure_form_v2', get_table_name(DASHBOARD_DOMAIN, 'static-infrastructure_form_v2'))
        ]
        for table in table_list:
            self.drop_temp_tables(table[0])
            self.create_temp_tables(table, day)
