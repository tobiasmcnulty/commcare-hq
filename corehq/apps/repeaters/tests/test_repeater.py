from collections import namedtuple
from StringIO import StringIO
from datetime import datetime, timedelta
from mock import patch

from casexml.apps.case.models import CommCareCase
from casexml.apps.case.mock import CaseBlock, CaseFactory
from casexml.apps.case.xml import V1

from django.core.urlresolvers import reverse
from django.test import TestCase
from django.test.client import Client

from corehq.apps.app_manager.tests.util import TestXmlMixin
from corehq.apps.domain.shortcuts import create_domain
from corehq.apps.receiverwrapper.exceptions import DuplicateFormatException, IgnoreDocument
from corehq.apps.repeaters.tasks import check_repeaters
from corehq.apps.repeaters.models import (
    CaseRepeater,
    FormRepeater,
    RepeatRecord,
    RegisterGenerator)
from corehq.apps.repeaters.repeater_generators import BasePayloadGenerator
from corehq.apps.repeaters.const import MIN_RETRY_WAIT
from couchforms.models import XFormInstance
from couchforms.const import DEVICE_LOG_XMLNS

MockResponse = namedtuple('MockResponse', 'status_code')
case_id = "ABC123CASEID"
instance_id = "XKVB636DFYL38FNX3D38WV5EH"
update_instance_id = "ZYXKVB636DFYL38FNX3D38WV5"

case_block = """
<case>
    <case_id>%s</case_id>
    <date_modified>2011-12-19T00:00:00.000000Z</date_modified>
    <create>
        <case_type_id>repeater_case</case_type_id>
        <user_id>O2XLT0WZW97W1A91E2W1Y0NJG</user_id>
        <case_name>ABC 123</case_name>
        <external_id>ABC 123</external_id>
    </create>
</case>
""" % case_id

update_block = """
<case>
    <case_id>%s</case_id>
    <date_modified>2011-12-19T00:00:00.000000Z</date_modified>
    <update>
        <case_name>ABC 234</case_name>
    </update>
</case>
""" % case_id


xform_xml_template = """<?xml version='1.0' ?>
<data xmlns:jrm="http://dev.commcarehq.org/jr/xforms" xmlns="{}">
    <woman_name>Alpha</woman_name>
    <husband_name>Beta</husband_name>
    <meta>
        <deviceID>O2XLT0WZW97W1A91E2W1Y0NJG</deviceID>
        <timeStart>2011-10-01T15:25:18.404-04</timeStart>
        <timeEnd>2011-10-01T15:26:29.551-04</timeEnd>
        <username>admin</username>
        <userID>O2XLT0WZW97W1A91E2W1Y0NJG</userID>
        <instanceID>{}</instanceID>
    </meta>
{}
</data>
"""
xform_xml = xform_xml_template.format(
    "https://www.commcarehq.org/test/repeater/",
    instance_id,
    case_block,
)
update_xform_xml = xform_xml_template.format(
    "https://www.commcarehq.org/test/repeater/",
    update_instance_id,
    update_block,
)


class BaseRepeaterTest(TestCase):
    client = Client()

    @classmethod
    def post_xml(cls, xml, domain_name):
        f = StringIO(xml)
        f.name = 'form.xml'
        cls.client.post(
            reverse('receiver_post', args=[domain_name]), {
                'xml_submission_file': f
            }
        )

    @classmethod
    def repeat_records(cls, domain_name):
        return RepeatRecord.all(domain=domain_name, due_before=datetime.utcnow())


class RepeaterTest(BaseRepeaterTest):
    def setUp(self):

        self.domain = "test-domain"
        create_domain(self.domain)
        self.case_repeater = CaseRepeater(
            domain=self.domain,
            url='case-repeater-url',
            version=V1,
        )
        self.case_repeater.save()
        self.form_repeater = FormRepeater(
            domain=self.domain,
            url='form-repeater-url',
        )
        self.form_repeater.save()
        self.log = []
        self.post_xml(xform_xml, self.domain)

    def tearDown(self):
        self.case_repeater.delete()
        self.form_repeater.delete()
        XFormInstance.get(instance_id).delete()
        repeat_records = RepeatRecord.all()
        for repeat_record in repeat_records:
            repeat_record.delete()

    def test_skip_device_logs(self):
        devicelog_xml = xform_xml_template.format(DEVICE_LOG_XMLNS, '1234', '')
        self.post_xml(devicelog_xml, self.domain)
        repeat_records = RepeatRecord.all(domain=self.domain)
        for repeat_record in repeat_records:
            self.assertNotEqual(repeat_record.payload_id, '1234')

    def test_repeater_failed_sends(self):
        """
        This tests records that fail to send three times
        """
        def now():
            return datetime.utcnow()

        repeat_records = RepeatRecord.all(domain=self.domain, due_before=now())
        self.assertEqual(len(repeat_records), 2)

        for repeat_record in repeat_records:
            with patch(
                    'corehq.apps.repeaters.models.simple_post_with_cached_timeout',
                    return_value=MockResponse(status_code=404)) as mock_post:
                repeat_record.fire()
                self.assertEqual(mock_post.call_count, 3)
            repeat_record.save()

        next_check_time = now() + timedelta(minutes=60)

        repeat_records = RepeatRecord.all(
            domain=self.domain,
            due_before=now() + timedelta(minutes=15),
        )
        self.assertEqual(len(repeat_records), 0)

        repeat_records = RepeatRecord.all(
            domain=self.domain,
            due_before=next_check_time,
        )
        self.assertEqual(len(repeat_records), 2)

    def test_update_failure_next_check(self):
        now = datetime.utcnow()
        record = RepeatRecord(domain=self.domain, next_check=now)
        self.assertIsNone(record.last_checked)

        record.update_failure()
        self.assertTrue(record.last_checked > now)
        self.assertEqual(record.next_check, record.last_checked + MIN_RETRY_WAIT)

    def test_repeater_successful_send(self):

        repeat_records = RepeatRecord.all(domain=self.domain, due_before=datetime.utcnow())
        mocked_responses = [MockResponse(status_code=404), MockResponse(status_code=200)]
        for repeat_record in repeat_records:
            with patch(
                    'corehq.apps.repeaters.models.simple_post_with_cached_timeout',
                    side_effect=mocked_responses) as mock_post:
                repeat_record.fire()
                self.assertEqual(mock_post.call_count, 2)
                mock_post.assert_any_call(
                    repeat_record.get_payload(),
                    repeat_record.repeater.get_url(repeat_record),
                    headers=repeat_record.repeater.get_headers(repeat_record),
                    force_send=False,
                )
            repeat_record.save()

        # The following is pretty fickle and depends on which of
        #   - corehq.apps.repeaters.signals
        #   - casexml.apps.case.signals
        # gets loaded first.
        # This is deterministic but easily affected by minor code changes
        repeat_records = RepeatRecord.all(
            domain=self.domain,
            due_before=datetime.utcnow(),
        )
        for repeat_record in repeat_records:
            self.assertEqual(repeat_record.succeeded, True)
            self.assertEqual(repeat_record.next_check, None)

        self.assertEqual(len(self.repeat_records(self.domain)), 0)

        self.post_xml(update_xform_xml, self.domain)
        self.assertEqual(len(self.repeat_records(self.domain)), 2)

    def test_check_repeat_records(self):
        self.assertEqual(len(RepeatRecord.all()), 2)

        with patch('corehq.apps.repeaters.models.simple_post_with_cached_timeout') as mock_fire:
            check_repeaters()
            self.assertEqual(mock_fire.call_count, 2)

        with patch('corehq.apps.repeaters.models.simple_post_with_cached_timeout') as mock_fire:
            check_repeaters()
            self.assertEqual(mock_fire.call_count, 0)


class CaseRepeaterTest(BaseRepeaterTest, TestXmlMixin):
    @classmethod
    def setUpClass(cls):
        cls.domain_name = "test-domain"
        cls.domain = create_domain(cls.domain_name)
        cls.repeater = CaseRepeater(
            domain=cls.domain_name,
            url="case-repeater-url",
        )
        cls.repeater.save()

    @classmethod
    def tearDownClass(cls):
        cls.domain.delete()
        cls.repeater.delete()

    def tearDown(self):
        try:
            # delete case, so that post of xform_xml creates new case as expected multiple-times
            CommCareCase.get(case_id).delete()
        except:
            pass
        for repeat_record in self.repeat_records(self.domain_name):
            repeat_record.delete()

    def test_case_close_format(self):
        # create a case
        self.post_xml(xform_xml, self.domain_name)
        payload = self.repeat_records(self.domain_name).all()[0].get_payload()
        self.assertXmlHasXpath(payload, '//*[local-name()="case"]')
        self.assertXmlHasXpath(payload, '//*[local-name()="create"]')

        # close the case
        CaseFactory().close_case(case_id)
        close_payload = self.repeat_records(self.domain_name).all()[1].get_payload()
        self.assertXmlHasXpath(close_payload, '//*[local-name()="case"]')
        self.assertXmlHasXpath(close_payload, '//*[local-name()="close"]')
        self.assertXmlHasXpath(close_payload, '//*[local-name()="update"]')

    def test_excluded_case_types_are_not_forwarded(self):
        self.repeater.white_listed_case_types = ['planet']
        self.repeater.save()

        white_listed_case = CaseBlock(
            case_id="a_case_id",
            create=True,
            case_type="planet",
        ).as_xml()
        CaseFactory(self.domain_name).post_case_blocks([white_listed_case])
        self.assertEqual(1, len(self.repeat_records(self.domain_name).all()))

        non_white_listed_case = CaseBlock(
            case_id="b_case_id",
            create=True,
            case_type="cat",
        ).as_xml()
        CaseFactory(self.domain_name).post_case_blocks([non_white_listed_case])
        self.assertEqual(1, len(self.repeat_records(self.domain_name).all()))

    def test_black_listed_user_cases_do_not_forward(self):
        self.repeater.black_listed_users = ['black_listed_user']
        self.repeater.save()

        # case-creations by black-listed users shouldn't be forwarded
        black_listed_user_case = CaseBlock(
            case_id="b_case_id",
            create=True,
            case_type="planet",
            owner_id="owner",
            user_id="black_listed_user"
        ).as_xml()
        CaseFactory(self.domain_name).post_case_blocks([black_listed_user_case])
        self.assertEqual(0, len(self.repeat_records(self.domain_name).all()))

        # case-creations by normal users should be forwarded
        normal_user_case = CaseBlock(
            case_id="a_case_id",
            create=True,
            case_type="planet",
            owner_id="owner",
            user_id="normal_user"
        ).as_xml()
        CaseFactory(self.domain_name).post_case_blocks([normal_user_case])
        self.assertEqual(1, len(self.repeat_records(self.domain_name).all()))

        # case-updates by black-listed users shouldn't be forwarded
        black_listed_user_case = CaseBlock(
            case_id="b_case_id",
            case_type="planet",
            owner_id="owner",
            user_id="black_listed_user",
        ).as_xml()
        CaseFactory(self.domain_name).post_case_blocks([black_listed_user_case])
        self.assertEqual(1, len(self.repeat_records(self.domain_name).all()))

        # case-updates by normal users should be forwarded
        normal_user_case = CaseBlock(
            case_id="a_case_id",
            case_type="planet",
            owner_id="owner",
            user_id="normal_user",
        ).as_xml()
        CaseFactory(self.domain_name).post_case_blocks([normal_user_case])
        self.assertEqual(2, len(self.repeat_records(self.domain_name).all()))


class RepeaterFailureTest(BaseRepeaterTest):

    def setUp(self):
        self.domain_name = "test-domain"
        self.domain = create_domain(self.domain_name)

        self.repeater = CaseRepeater(
            domain=self.domain_name,
            url='case-repeater-url',
            version=V1,
            format='other_format'
        )
        self.repeater.save()
        self.post_xml(xform_xml, self.domain)

    def tearDown(self):
        self.domain.delete()
        self.repeater.delete()
        repeat_records = RepeatRecord.all()
        for repeat_record in repeat_records:
            repeat_record.delete()

    def test_failure(self):
        payload = "some random case"

        @RegisterGenerator(CaseRepeater, 'other_format', 'XML')
        class NewCaseGenerator(BasePayloadGenerator):
            def get_payload(self, repeat_record, payload_doc):
                return payload

        repeat_record = self.repeater.register(CommCareCase.get(case_id))
        with patch('corehq.apps.repeaters.models.simple_post_with_cached_timeout', side_effect=Exception('Boom!')):
            repeat_record.fire()

        self.assertEquals(repeat_record.failure_reason, 'Boom!')
        self.assertFalse(repeat_record.succeeded)

        # Should be marked as successful after a successful run
        with patch('corehq.apps.repeaters.models.simple_post_with_cached_timeout'):
            repeat_record.fire()

        self.assertTrue(repeat_record.succeeded)


class IgnoreDocumentTest(BaseRepeaterTest):

    def setUp(self):
        self.domain = "test-domain"
        create_domain(self.domain)

        self.repeater = FormRepeater(
            domain=self.domain,
            url='form-repeater-url',
            version=V1,
            format='new_format'
        )
        self.repeater.save()

    def tearDown(self):
        self.repeater.delete()
        repeat_records = RepeatRecord.all()
        for repeat_record in repeat_records:
            repeat_record.delete()

    def test_ignore_document(self):
        """
        When get_payload raises IgnoreDocument, fire should call update_success
        """

        @RegisterGenerator(FormRepeater, 'new_format', 'XML')
        class NewFormGenerator(BasePayloadGenerator):
            def get_payload(self, repeat_record, payload_doc):
                raise IgnoreDocument

        repeat_records = RepeatRecord.all(
            domain=self.domain,
        )
        for repeat_record_ in repeat_records:
            repeat_record_.fire()

            self.assertIsNone(repeat_record_.next_check)
            self.assertTrue(repeat_record_.succeeded)


class TestRepeaterFormat(BaseRepeaterTest):
    def setUp(self):
        self.domain = "test-domain"
        create_domain(self.domain)
        self.post_xml(xform_xml, self.domain)

        self.repeater = CaseRepeater(
            domain=self.domain,
            url='case-repeater-url',
            version=V1,
            format='new_format'
        )
        self.repeater.save()

    def tearDown(self):
        self.repeater.delete()
        XFormInstance.get(instance_id).delete()
        repeat_records = RepeatRecord.all()
        for repeat_record in repeat_records:
            repeat_record.delete()

    def test_new_format_same_name(self):
        with self.assertRaises(DuplicateFormatException):
            @RegisterGenerator(CaseRepeater, 'case_xml', 'XML', is_default=False)
            class NewCaseGenerator(BasePayloadGenerator):
                def get_payload(self, repeat_record, payload_doc):
                    return "some random case"

    def test_new_format_second_default(self):
        with self.assertRaises(DuplicateFormatException):
            @RegisterGenerator(CaseRepeater, 'rubbish', 'XML', is_default=True)
            class NewCaseGenerator(BasePayloadGenerator):
                def get_payload(self, repeat_record, payload_doc):
                    return "some random case"

    def test_new_format_payload(self):
        payload = "some random case"

        @RegisterGenerator(CaseRepeater, 'new_format', 'XML')
        class NewCaseGenerator(BasePayloadGenerator):
            def get_payload(self, repeat_record, payload_doc):
                return payload

        repeat_record = self.repeater.register(CommCareCase.get(case_id))
        with patch('corehq.apps.repeaters.models.simple_post_with_cached_timeout') as mock_post:
            repeat_record.fire()
            headers = self.repeater.get_headers(repeat_record)
            mock_post.assert_called_with(
                payload,
                self.repeater.url,
                headers=headers,
                force_send=False
            )
