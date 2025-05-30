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

from woob.capabilities.base import NotAvailable, find_object
from woob.capabilities.bill import (
    CapDocument,
    Document,
    DocumentCategory,
    DocumentNotFound,
    DocumentTypes,
    Subscription,
)
from woob.tools.backend import BackendConfig
from woob.tools.value import Value, ValueBackendPassword
from woob_modules.franceconnect.module import FranceConnectModule

from .browser import PajemploiBrowser


__all__ = ["PajemploiModule"]


class PajemploiModule(FranceConnectModule, CapDocument):
    NAME = "pajemploi"
    DESCRIPTION = (
        "Pajemploi est une offre de service du réseau des Urssaf"
        ", destinée à simplifier les formalités administratives pour les "
        "parents employeurs qui font garder leur(s) enfant(s) par une "
        "assistante maternelle agréée ou une garde d’enfants à domicile."
    )
    MAINTAINER = "Ludovic LANGE"
    EMAIL = "llange@users.noreply.github.com"
    LICENSE = "LGPLv3+"
    DEPENDENCIES = ("franceconnect",)

    CONFIG = BackendConfig(
        Value("username", label="User ID"),
        ValueBackendPassword("password", label="Password"),
        Value(
            "login_source",
            label="Méthode d'authentification",
            default="direct",
            choices={
                "direct": "Directe",
                "fc_impots": "France Connect Impôts",
            },
        ),
    )
    BROWSER = PajemploiBrowser

    accepted_document_types = (
        DocumentTypes.STATEMENT,
        DocumentTypes.CERTIFICATE,
    )
    document_categories = {DocumentCategory.ADMINISTRATIVE}

    def create_default_browser(self):
        return self.create_browser(self.config, self.config["username"].get(), self.config["password"].get())

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)

        if document.url is NotAvailable:
            return
        return self.browser.download_document(document)

    def get_document(self, _id):
        subscription_id = _id.split("_")[0]
        subscription = self.get_subscription(subscription_id)
        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

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
