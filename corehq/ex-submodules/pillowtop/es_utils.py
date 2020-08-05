from corehq.util.es.interface import ElasticsearchInterface
from dimagi.ext import jsonobject
from django.conf import settings
from copy import copy, deepcopy
from datetime import datetime
from corehq.util.es.elasticsearch import TransportError
from pillowtop.logger import pillow_logging


def _get_analysis(*names):
    return {
        "analyzer": {name: ANALYZERS[name] for name in names}
    }


ANALYZERS = {
    "default": {
        "type": "custom",
        "tokenizer": "whitespace",
        "filter": ["lowercase"]
    },
    "comma": {
        "type": "pattern",
        "pattern": r"\s*,\s*"
    }
}

REMOVE_SETTING = None

ES_ENV_SETTINGS = {
    'icds': {
        'hqusers': {
            "number_of_replicas": 1,
        },
    },
}

XFORM_HQ_INDEX_NAME = "xforms"
CASE_HQ_INDEX_NAME = "hqcases"
USER_HQ_INDEX_NAME = "hqusers"
DOMAIN_HQ_INDEX_NAME = "hqdomains"
APP_HQ_INDEX_NAME = "hqapps"
GROUP_HQ_INDEX_NAME = "hqgroups"
SMS_HQ_INDEX_NAME = "smslogs"
REPORT_CASE_HQ_INDEX_NAME = "report_cases"
REPORT_XFORM_HQ_INDEX_NAME = "report_xforms"
CASE_SEARCH_HQ_INDEX_NAME = "case_search"
TEST_HQ_INDEX_NAME = "pillowtop_tests"

ES_INDEX_SETTINGS = {
    # Default settings for all indexes on ElasticSearch
    'default': {
        "settings": {
            "number_of_replicas": 0,
            "analysis": _get_analysis('default'),
        },
    },
    # Default settings for aliases on all environments (overrides default settings)
    DOMAIN_HQ_INDEX_NAME: {
        "settings": {
            "number_of_replicas": 0,
            "analysis": _get_analysis('default', 'comma'),
        },
    },

    APP_HQ_INDEX_NAME: {
        "settings": {
            "number_of_replicas": 0,
            "analysis": _get_analysis('default'),
        },
    },

    USER_HQ_INDEX_NAME: {
        "settings": {
            "number_of_shards": 2,
            "number_of_replicas": 0,
            "analysis": _get_analysis('default'),
        },
    },
}


class ElasticsearchIndexInfo(jsonobject.JsonObject):
    index = jsonobject.StringProperty(required=True)
    alias = jsonobject.StringProperty()
    type = jsonobject.StringProperty()
    mapping = jsonobject.DictProperty()
    hq_index_name = jsonobject.StringProperty()

    def __str__(self):
        return '{} ({})'.format(self.alias, self.index)

    @property
    def meta(self):
        meta_settings = deepcopy(ES_INDEX_SETTINGS['default'])
        meta_settings.update(
            ES_INDEX_SETTINGS.get(self.hq_index_name, {})
        )
        meta_settings.update(
            ES_INDEX_SETTINGS.get(settings.SERVER_ENVIRONMENT, {}).get(self.hq_index_name, {})
        )

        overrides = copy(ES_ENV_SETTINGS)
        if settings.ES_SETTINGS is not None:
            overrides.update({settings.SERVER_ENVIRONMENT: settings.ES_SETTINGS})

        for hq_index_name in ['default', self.hq_index_name]:
            for key, value in overrides.get(settings.SERVER_ENVIRONMENT, {}).get(hq_index_name, {}).items():
                if value is REMOVE_SETTING:
                    del meta_settings['settings'][key]
                else:
                    meta_settings['settings'][key] = value

        return meta_settings

    def to_json(self):
        json = super(ElasticsearchIndexInfo, self).to_json()
        json['meta'] = self.meta
        return json


def set_index_reindex_settings(es, index):
    """
    Set a more optimized setting setup for fast reindexing
    """
    from pillowtop.index_settings import INDEX_REINDEX_SETTINGS
    return ElasticsearchInterface(es).update_index_settings(index, INDEX_REINDEX_SETTINGS)


def set_index_normal_settings(es, index):
    """
    Normal indexing configuration
    """
    from pillowtop.index_settings import INDEX_STANDARD_SETTINGS
    return ElasticsearchInterface(es).update_index_settings(index, INDEX_STANDARD_SETTINGS)


def create_index_and_set_settings_normal(es, index, metadata=None):
    metadata = metadata or {}
    es.indices.create(index=index, body=metadata)
    set_index_normal_settings(es, index)


def initialize_index_and_mapping(es, index_info):
    index_exists = es.indices.exists(index_info.index)
    if not index_exists:
        initialize_index(es, index_info)
    initialize_mapping_if_necessary(es, index_info)
    assume_alias(es, index_info.index, index_info.alias)


def initialize_index(es, index_info):
    return create_index_and_set_settings_normal(es, index_info.index, index_info.meta)


def mapping_exists(es, index_info):
    try:
        if settings.ELASTICSEARCH_MAJOR_VERSION == 7:
            return es.indices.get_mapping(index_info.index).get(index_info.index, {}).get('mappings', None)
        else:
            return es.indices.get_mapping(index_info.index, index_info.type)
    except TransportError:
        return {}


def initialize_mapping_if_necessary(es, index_info):
    """
    Initializes the elasticsearch mapping for this pillow if it is not found.
    """
    es_interface = ElasticsearchInterface(es)
    if not mapping_exists(es, index_info):
        pillow_logging.info("Initializing elasticsearch mapping for [%s]" % index_info.type)
        mapping = copy(index_info.mapping)
        mapping['_meta']['created'] = datetime.isoformat(datetime.utcnow())
        mapping_res = es_interface.put_mapping(index_info.type, mapping, index_info.index)
        if mapping_res.get('ok', False) and mapping_res.get('acknowledged', False):
            # API confirms OK, trust it.
            pillow_logging.info("Mapping set: [%s] %s" % (index_info.type, mapping_res))
    else:
        pillow_logging.info("Elasticsearch mapping for [%s] was already present." % index_info.type)


def assume_alias(es, index, alias):
    """
    This operation assigns the alias to the index and removes the alias
    from any other indices it might be assigned to.
    """
    if es.indices.exists_alias(name=alias):
        # this part removes the conflicting aliases
        alias_indices = list(es.indices.get_alias(alias))
        for aliased_index in alias_indices:
            es.indices.delete_alias(aliased_index, alias)
    es.indices.put_alias(index, alias)


def doc_exists(pillow, doc_id_or_dict):
    index_info = get_index_info_from_pillow(pillow)
    from corehq.elastic import doc_exists_in_es
    return doc_exists_in_es(index_info, doc_id_or_dict)


def get_index_info_from_pillow(pillow):
    return ElasticsearchIndexInfo(
        index=pillow.es_index,
        alias=pillow.es_alias,
        type=pillow.es_type,
        meta=pillow.es_meta,
        mapping=pillow.default_mapping,
    )
