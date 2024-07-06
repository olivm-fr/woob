# Copyright(C) 2018      Phyks (Lucas Verney)
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

# flake8: compatible

from woob.capabilities.profile import CapProfile
from woob.tools.backend import Module, BackendConfig
from woob.tools.value import Value, ValueBackendPassword
from woob.capabilities.base import find_object
from woob.capabilities.bill import (
    CapDocument, Document, DocumentNotFound, Subscription, DocumentTypes, DocumentCategory,
)

from .browser import EkwateurBrowser


__all__ = ['EkwateurModule']


class EkwateurModule(Module, CapDocument, CapProfile):
    NAME = 'ekwateur'
    DESCRIPTION = 'ekwateur website'
    MAINTAINER = 'Phyks (Lucas Verney)'
    EMAIL = 'phyks@phyks.me'
    LICENSE = 'LGPLv3+'
    VERSION = '3.6'

    BROWSER = EkwateurBrowser

    CONFIG = BackendConfig(
        Value('login', help='Email or identifier'),
        ValueBackendPassword('password', help='Password'),
    )

    accepted_document_types = (DocumentTypes.BILL,)
    document_categories = {DocumentCategory.ENERGY}

    def create_default_browser(self):
        return self.create_browser(self.config['login'].get(), self.config['password'].get())

    def get_document(self, id):
        """
        Get a document.

        :param id: ID of document
        :rtype: :class:`Document`
        :raises: :class:`DocumentNotFound`
        """
        return find_object(
            self.iter_documents(id.split("#")[-1]),
            id=id,
            error=DocumentNotFound
        )

    def download_document(self, doc):
        if not isinstance(doc, Document):
            doc = self.get_document(doc)

        if not doc.url:
            return None

        return self.browser.open(doc.url).content

    def iter_documents(self, subscription):
        """
        Iter documents.

        :param subscription: subscription to get documents
        :type subscription: :class:`Subscription`
        :rtype: iter[:class:`Document`]
        """
        if isinstance(subscription, Subscription):
            subscription = subscription.id
        return self.browser.iter_documents(subscription)

    def iter_subscription(self):
        """
        Iter subscriptions.

        :rtype: iter[:class:`Subscription`]
        """
        return self.browser.iter_subscriptions()

    def get_profile(self):
        return self.browser.get_profile()
