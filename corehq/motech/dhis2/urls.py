from django.conf.urls import url

from corehq.motech.dhis2.views import DataSetMapListView, send_dhis2_data
from corehq.motech.views import ConnectionSettingsView

urlpatterns = [
    url(r'^conn/$', ConnectionSettingsView.as_view(), name=ConnectionSettingsView.urlname),
    url(r'^map/$', DataSetMapListView.as_view(), name=DataSetMapListView.urlname),
    url(r'^send/$', send_dhis2_data, name='send_dhis2_data'),
]
