from __future__ import absolute_import
from __future__ import unicode_literals

from django.urls import reverse
from django.utils.functional import cached_property

from corehq.blobs import (
    get_blob_db,
    CODES,
)
from corehq.blobs.models import BlobMeta


class CCZHostingUtility:
    def __init__(self, ccz_hosting=None, blob_id=None):
        """
        utils for ccz file actions either via ccz hosting or a blob object id
        """
        self.ccz_hosting = ccz_hosting
        self.blob_id = blob_id
        if ccz_hosting and not blob_id:
            self.blob_id = self.ccz_hosting.blob_id

    def file_exists(self):
        return get_blob_db().exists(key=self.blob_id)

    def get_file(self):
        return get_blob_db().get(key=self.blob_id)

    def get_file_size(self):
        return get_blob_db().size(key=self.blob_id)

    @cached_property
    def get_file_meta(self):
        if self.file_exists():
            return get_blob_db().metadb.get(key=self.blob_id, parent_id='CCZHosting')

    @property
    def ccz_details(self):
        file_name = ""
        if self.ccz_hosting:
            file_name = self.ccz_hosting.file_name
        elif self.get_file_meta:
            file_name = self.get_file_meta.name
        return {
            'name': file_name,
            'download_url': reverse('ccz_hosting_download_ccz', args=[
                self.ccz_hosting.domain, self.ccz_hosting.id, self.blob_id])
        }

    def store_file_in_blobdb(self, ccz_file, name):
        db = get_blob_db()
        try:
            kw = {"meta": db.metadb.get(parent_id='CCZHosting', key=self.blob_id)}
        except BlobMeta.DoesNotExist:
            kw = {
                "domain": self.ccz_hosting.link.domain,
                "parent_id": 'CCZHosting',
                "type_code": CODES.tempfile,
                "key": self.blob_id,
                "name": name,
            }
        db.put(ccz_file, **kw)

    def remove_file_from_blobdb(self):
        get_blob_db().delete(key=self.blob_id)
