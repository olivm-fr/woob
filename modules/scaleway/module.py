# Copyright(C) 2022      Jeremy Demange (scrapfast.io)
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

from woob.capabilities.base import NotAvailable, find_object
from woob.capabilities.bill import (
    CapDocument,
    Document,
    DocumentCategory,
    DocumentNotFound,
    DocumentTypes,
    Subscription,
)
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import Value, ValueBackendPassword

from .browser import ScalewayBrowser


__all__ = ["ScalewayModule"]


class ScalewayModule(Module, CapDocument):
    NAME = "scaleway"
    DESCRIPTION = "Scaleway"
    MAINTAINER = "Jeremy Demange + Ludovic LANGE"
    EMAIL = "jeremy@scrapfast.io"
    LICENSE = "LGPLv3+"
    CONFIG = BackendConfig(
        Value("access_key", label="Access Key"),
        ValueBackendPassword("secret_key", label="Secret Key"),
    )

    BROWSER = ScalewayBrowser

    accepted_document_types = (
        DocumentTypes.BILL,
        DocumentTypes.OTHER,
    )
    document_categories = {DocumentCategory.SOFTWARE}

    def create_default_browser(self):
        return self.create_browser(self.config)

    def iter_subscription(self):
        return self.browser.get_subscription_list()

    def get_document(self, _id):
        return find_object(self.iter_documents(), id=_id, error=DocumentNotFound)

    def iter_documents(self, subscription=""):
        if isinstance(subscription, Subscription):
            subscription = subscription.id
        return self.browser.iter_documents(subscription)

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        if document.url is NotAvailable:
            return
        return self.browser.download_document(document)
