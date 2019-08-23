#!/usr/bin/env python
import os
import sys

import attr
from gevent import monkey
from psycogreen.gevent import patch_psycopg


@attr.s
class GeventCommand(object):
    command = attr.ib()
    contains = attr.ib(default=None)
    http_adapter_pool_size = attr.ib(default=None)


def _set_source_root_parent(source_root_parent):
    """
    add everything under `source_root_parent` to the list of source roots
    e.g. if you call this with param 'submodules'
    and you have the file structure

    project/
        submodules/
            foo-src/
                foo/
            bar-src/
                bar/

    (where foo and bar are python modules)
    then foo and bar would become top-level importable

    """
    filedir = os.path.dirname(__file__)
    submodules_list = os.listdir(os.path.join(filedir, source_root_parent))
    for d in submodules_list:
        if d == "__init__.py" or d == '.' or d == '..':
            continue
        sys.path.insert(1, os.path.join(filedir, source_root_parent, d))

    sys.path.append(os.path.join(filedir, source_root_parent))


def _set_source_root(source_root):
    filedir = os.path.dirname(__file__)
    sys.path.insert(1, os.path.join(filedir, source_root))


# HACK monkey-patch django setup to prevent second setup by django_nose
def _setup_once(*args, **kw):
    if not hasattr(_setup_once, "done"):
        _setup_once.done = True
        _setup_once.setup(*args, **kw)


def init_hq_python_path():
    _set_source_root_parent('submodules')
    _set_source_root(os.path.join('corehq', 'ex-submodules'))
    _set_source_root(os.path.join('custom', '_legacy'))


def _should_patch_gevent(args, gevent_commands):
    should_patch = False
    for gevent_command in gevent_commands:
        should_patch = args[1] == gevent_command.command
        if gevent_command.contains:
            should_patch = should_patch and gevent_command.contains in ' '.join(args)
        if should_patch:
            if gevent_command.http_adapter_pool_size:
                import requests
                requests.adapters.DEFAULT_POOLSIZE = gevent_command.http_adapter_pool_size
            break
    return should_patch


def set_default_settings_path(argv):
    if len(argv) > 1 and argv[1] == 'test' or os.environ.get('CCHQ_TESTING') == '1':
        os.environ.setdefault('CCHQ_TESTING', '1')
        module = 'testsettings'
    else:
        module = 'settings'
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", module)


def set_nosetests_verbosity(argv):
    """Increase nose output verbosity with -v... argument

        -v: print test names
        -vv: do not capture stdout
        -vvv: do not capture logging
        -vvvv: enable nose internal logging
    """
    import logging

    def set_verbosity(arg, i):
        args = []
        verbosity = sum(1 for c in arg if c == "v") + 1
        if len(arg) > verbosity:
            # preserve other single-letter arguments (ex: -xv)
            args.append("".join(c for c in arg if c != "v"))
        if verbosity > 2:
            args.append("--nocapture")
        if verbosity > 3:
            verbosity -= 1
            args.append("--nologcapture")
            logging.basicConfig(level=logging.NOTSET)
            logging.getLogger().info(
                "Adjust logging with testsettings._set_logging_levels")
        args.append("--nose-verbosity=%s" % verbosity)
        argv[i:i + 1] = args

    if len(argv) > 1 and argv[1] == 'test':
        for i, arg in reversed(list(enumerate(argv))):
            if arg[:1] == "-" and arg[1] != "-" and any(c == 'v' for c in arg):
                set_verbosity(arg, i)
                break


def patch_jsonfield():
    """Patch the ``to_python`` method of JSONField
    See https://github.com/bradjasper/django-jsonfield/pull/173 for more details
    """
    import six
    import json
    from django.core.exceptions import ValidationError
    from django.utils.translation import ugettext_lazy as _
    from jsonfield import JSONField

    def to_python(self, value):
        if isinstance(value, six.string_types):
            try:
                return json.loads(value, **self.load_kwargs)
            except ValueError:
                raise ValidationError(_("Enter valid JSON"))
        return value

    JSONField.to_python = to_python


def patch_assertItemsEqual():
    import six
    import unittest
    if six.PY3:
        unittest.TestCase.assertItemsEqual = unittest.TestCase.assertCountEqual


def patch_pickle_version():
    # to avoid incompatibility between python 2 and 3
    import pickle
    pickle.HIGHEST_PROTOCOL = 2


def run_patches():
    # workaround for https://github.com/smore-inc/tinys3/issues/33
    import mimetypes
    mimetypes.init()

    patch_jsonfield()

    patch_assertItemsEqual()

    # After PY3 migration: remove
    patch_pickle_version()

    import django
    _setup_once.setup = django.setup
    django.setup = _setup_once


if __name__ == "__main__":
    # important to apply gevent monkey patches before running any other code
    # applying this later can lead to inconsistencies and threading issues
    # but compressor doesn't like it
    # ('module' object has no attribute 'poll' which has to do with
    # gevent-patching subprocess)
    GEVENT_COMMANDS = (
        GeventCommand('run_gunicorn'),
        GeventCommand('run_sql'),
        GeventCommand('run_blob_migration'),
        GeventCommand('check_blob_logs'),
        GeventCommand('preindex_everything'),
        GeventCommand('migrate_multi'),
        GeventCommand('prime_views'),
        GeventCommand('ptop_preindex'),
        GeventCommand('sync_prepare_couchdb_multi'),
        GeventCommand('sync_couch_views'),
        GeventCommand('populate_form_date_modified'),
        GeventCommand('migrate_domain_from_couch_to_sql', http_adapter_pool_size=32),
        GeventCommand('migrate_multiple_domains_from_couch_to_sql', http_adapter_pool_size=32),
        GeventCommand('run_aggregation_query'),
        GeventCommand('send_pillow_retry_queue_through_pillows'),
    )
    if len(sys.argv) > 1 and _should_patch_gevent(sys.argv, GEVENT_COMMANDS):
        monkey.patch_all(subprocess=True)
        patch_psycopg()

    init_hq_python_path()
    run_patches()

    set_default_settings_path(sys.argv)
    set_nosetests_verbosity(sys.argv)
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
