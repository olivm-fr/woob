# -*- coding: utf-8 -*-

# Copyright(C) 2020      Ludovic LANGE
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals


from weboob.tools.backend import Module, BackendConfig
from weboob.tools.value import Value, ValueBackendPassword
from weboob.capabilities.bill import (
    DocumentTypes,
    CapDocument,
    Subscription,
    SubscriptionNotFound,
    DocumentNotFound,
    Document,
)
from weboob.capabilities.profile import CapProfile
from weboob.capabilities.base import find_object, NotAvailable

from .browser import AprilBrowser


__all__ = ["AprilModule"]


class AprilModule(Module, CapDocument, CapProfile):
    NAME = "april"
    DESCRIPTION = (
        "Pour les domaines d'assurance à titre individuel suivants "
        "(uniquement): Santé, de prêt, Prévoyance, Indépendance, Santé chien chat."
    )
    MAINTAINER = "Ludovic LANGE"
    EMAIL = "llange@users.noreply.github.com"
    LICENSE = "LGPLv3+"
    VERSION = "2.1"

    BROWSER = AprilBrowser

    CONFIG = BackendConfig(
        Value("username", help="Username"),
        ValueBackendPassword("password", help="Password"),
    )

    accepted_document_types = (
        DocumentTypes.CONTRACT,
        DocumentTypes.BILL,
        DocumentTypes.NOTICE,
        DocumentTypes.REPORT,
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

    def get_subscription(self, _id):
        return find_object(self.iter_subscription(), id=_id, error=SubscriptionNotFound)

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

    def get_profile(self):
        return self.browser.get_profile()
