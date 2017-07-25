from collections import defaultdict

from casexml.apps.case.exceptions import IllegalCaseId, InvalidCaseIndex, CaseValueError, PhoneDateValueError
from casexml.apps.case.exceptions import UsesReferrals
from casexml.apps.case.signals import case_post_save
from corehq.apps.commtrack.exceptions import MissingProductId
from corehq.blobs.mixin import bulk_atomic_blobs
from corehq.form_processor.backends.sql.dbaccessors import FormAccessorSQL, CaseAccessorSQL, LedgerAccessorSQL
from corehq.form_processor.change_publishers import publish_form_saved, publish_case_saved, publish_ledger_v2_saved
from corehq.form_processor.exceptions import XFormNotFound
from corehq.form_processor.interfaces.processor import FormProcessorInterface
from corehq.form_processor.models import XFormInstanceSQL, CaseTransaction, LedgerTransaction
from corehq.form_processor.submission_post import SubmissionPost, _transform_instance_to_error
from corehq.form_processor.utils.general import should_use_sql_backend
from corehq.form_processor.utils.xform import _get_form
from corehq.sql_db.util import get_db_alias_for_partitioned_doc
from couchforms.models import XFormInstance


def reprocess_unfinished_stub(stub):
    if stub.saved:
        # ignore for now
        return

    if not should_use_sql_backend(stub.domain):
        # ignore for couch domains
        stub.delete()
        return

    form_id = stub.xform_id
    try:
        form = FormAccessorSQL.get_form(form_id)
    except XFormNotFound:
        # form doesn't exist which means the failure probably happend during saving so
        # let mobile handle re-submitting it
        stub.delete()
        return

    _reprocess_form(form, raise_errors=False)

    stub.delete()


def reprocess_xform_error(form):
    """
    Attempt to re-process an error form. This was created specifically to address
    the issue of out of order forms and child cases (form creates child case before
    parent case has been created).

    See http://manage.dimagi.com/default.asp?250459
    :param form_id: ID of the error form to process
    """
    if not form:
        raise Exception('Form with ID {} not found'.format(form.form_id))

    if not form.is_error:
        raise Exception('Form was not an error form: {}={}'.format(form.form_id, form.doc_type))

    return _reprocess_form(form)


def reprocess_xform_error_by_id(form_id, domain=None):
    form = _get_form(form_id)
    if domain and form.domain != domain:
        raise Exception('Form not found')
    return reprocess_xform_error(form)


def _reprocess_form(form, raise_errors=True):
    # reset form state prior to processing
    if should_use_sql_backend(form.domain):
        form.state = XFormInstanceSQL.NORMAL
    else:
        form.doc_type = 'XFormInstance'

    form.initial_processing_complete = True
    form.problem = None

    interface = FormProcessorInterface(form.domain)
    cache = interface.casedb_cache(
        domain=form.domain, lock=True, deleted_ok=True, xforms=[form]
    )
    with cache as casedb:
        try:
            case_stock_result = SubmissionPost.process_xforms_for_cases([form], casedb)
        except (IllegalCaseId, UsesReferrals, MissingProductId,
                PhoneDateValueError, InvalidCaseIndex, CaseValueError) as e:
            if raise_errors:
                raise
            form = _transform_instance_to_error(interface, e, form)
            # this is usually just one document, but if an edit errored we want
            # to save the deprecated form as well
            interface.save_processed_models([form])
            return form

        if case_stock_result:
            stock_result = case_stock_result.stock_result
            assert stock_result.populated

            cases = case_stock_result.case_models
            if should_use_sql_backend(form.domain):
                cases = _filter_already_processed_cases(form, cases)
                for case in cases:
                    CaseAccessorSQL.save_case(case)

                ledgers = _filter_already_processed_ledgers(form, stock_result.models_to_save)
                LedgerAccessorSQL.save_ledger_values(ledgers)

                FormAccessorSQL.update_form_problem_and_state(form)
                publish_form_saved(form)
                for case in cases:
                    publish_case_saved(case)
                    case_post_save.send(case.__class__, case=case)
                for ledger in ledgers:
                    publish_ledger_v2_saved(ledger)

            else:
                with bulk_atomic_blobs([form] + cases):
                    XFormInstance.save(form)  # use this save to that we don't overwrite the doc_type
                    XFormInstance.get_db().bulk_save(cases)
                stock_result.commit()

            case_stock_result.stock_result.finalize()
            case_stock_result.case_result.commit_dirtiness_flags()

    return form


def _filter_already_processed_cases(form, cases):
    """Remove any cases that already have a case transaction for this form"""
    cases_by_id = {
        case.case_id: case
        for case in cases
        }
    case_dbs = defaultdict(list)
    for case in cases:
        db_name = get_db_alias_for_partitioned_doc(case.case_id)
        case_dbs[db_name].append(case.case_id)
    for db_name, case_ids in case_dbs.items():
        transactions = CaseTransaction.objects.using(db_name).filter(case_id__in=case_ids, form_id=form.form_id)
        for trans in transactions:
            del cases_by_id[trans.case_id]
    return cases_by_id.values()


def _filter_already_processed_ledgers(form, ledgers):
    """Remove any ledgers that already have a ledger transaction for this form"""
    ledgers_by_id = {
        ledger.ledger_reference: ledger
        for ledger in ledgers
    }
    ledger_dbs = defaultdict(list)
    for ledger in ledgers_by_id.values():
        db_name = get_db_alias_for_partitioned_doc(ledger.case_id)
        ledger_dbs[db_name].append(ledger.case_id)
    for db_name, case_ids in ledger_dbs.items():
        transactions = LedgerTransaction.objects.using().filter(case_id__in=case_ids, form_id=form.form_id)
        for trans in transactions:
            del ledgers_by_id[trans.ledger_reference]
    return ledgers_by_id.values()
