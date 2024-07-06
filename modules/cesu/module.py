# flake8: compatible

# Copyright(C) 2020      Ludovic LANGE
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


from woob.tools.backend import BackendConfig, Module
from woob.capabilities.bill import CapDocument, Document, DocumentNotFound, DocumentTypes, Subscription
from woob.capabilities.base import find_object, NotAvailable
from woob.tools.value import ValueBackendPassword, Value

from .browser import CesuBrowser


__all__ = ["CesuModule"]


class CesuModule(Module, CapDocument):
    NAME = "cesu"
    DESCRIPTION = "Le Cesu est une offre simplifiée pour déclarer facilement la rémunération de votre salarié à domicile pour des activités de service à la personne."
    MAINTAINER = "Ludovic LANGE"
    EMAIL = "llange@users.noreply.github.com"
    LICENSE = "LGPLv3+"
    VERSION = "3.6"

    CONFIG = BackendConfig(
        Value("username", label="User ID"),
        ValueBackendPassword("password", label="Password"),
    )

    BROWSER = CesuBrowser

    accepted_document_types = (
        DocumentTypes.STATEMENT,
        DocumentTypes.NOTICE,
        DocumentTypes.REPORT,
        DocumentTypes.PAYSLIP,
        DocumentTypes.CERTIFICATE,
        DocumentTypes.OTHER,
    )

    def create_default_browser(self):
        return self.create_browser(
            self.config["username"].get(), self.config["password"].get()
        )

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)

        if document.url is NotAvailable:
            return
        return self.browser.open(document.url).content

    def get_document(self, _id):
        subscription_id = _id.split("_")[0]
        subscription = self.get_subscription(subscription_id)
        return find_object(
            self.iter_documents(subscription), id=_id, error=DocumentNotFound
        )

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)

        return self.browser.iter_documents(subscription)

    def iter_resources(self, objs, split_path):
        if Subscription in objs:
            self._restrict_level(split_path)
            return self.iter_subscription()

    def iter_subscription(self):
        return self.browser.iter_subscription()
