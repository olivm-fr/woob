# Copyright(C) 2017      Vincent A

# flake8: compatible

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


from woob.capabilities.base import find_object
from woob.capabilities.bill import (
    CapDocument,
    Document,
    DocumentCategory,
    DocumentNotFound,
    DocumentTypes,
    Subscription,
)
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword, ValueTransient

from .browser import InfomaniakBrowser


__all__ = ["InfomaniakModule"]


class InfomaniakModule(Module, CapDocument):
    NAME = "infomaniak"
    DESCRIPTION = "Infomaniak"
    MAINTAINER = "Vincent A"
    EMAIL = "dev@indigo.re"
    LICENSE = "LGPLv3+"
    VERSION = "3.7"
    CONFIG = BackendConfig(
        ValueBackendPassword("login", label="Email de connexion", masked=False),
        ValueBackendPassword("password", label="Mot de passe"),
        ValueTransient("otp"),
    )

    BROWSER = InfomaniakBrowser

    accepted_document_types = (DocumentTypes.BILL,)
    document_categories = {DocumentCategory.SOFTWARE}

    def create_default_browser(self):
        return self.create_browser(self.config)

    def iter_subscription(self):
        return self.browser.iter_subscription()

    def get_document(self, _id):
        subid = _id.rsplit("_", 1)[0]
        subscription = self.get_subscription(subid)

        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)
        return self.browser.iter_documents(subscription)

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        return self.browser.open(document.url).content
