# -*- coding: utf-8 -*-

# Copyright(C) 2018      Vincent A
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


from woob.tools.backend import Module, BackendConfig
from woob.tools.value import Value, ValueBackendPassword
from woob.capabilities.base import find_object
from woob.capabilities.calendar import CapCalendarEvent
from woob.capabilities.bill import (
    CapDocument, DocumentCategory, DocumentTypes, DocumentNotFound, Subscription,
)

from .browser import LuccaBrowser


__all__ = ['LuccaModule']


class LuccaModule(Module, CapDocument, CapCalendarEvent):
    NAME = 'lucca'
    DESCRIPTION = 'Lucca'
    MAINTAINER = 'Vincent A'
    EMAIL = 'dev@indigo.re'
    LICENSE = 'LGPLv3+'
    VERSION = '3.6'

    BROWSER = LuccaBrowser

    CONFIG = BackendConfig(
        Value('subdomain', label='Sous-domaine', regexp=r'[\w-]+'),
        Value('login', label='Identifiant'),
        ValueBackendPassword('password', label='Mot de passe'),
    )

    accepted_document_types = (DocumentTypes.PAYSLIP,)
    document_categories = {DocumentCategory.SAFE_DEPOSIT_BOX}

    def create_default_browser(self):
        return self.create_browser(
            self.config['subdomain'].get(),
            self.config['login'].get(),
            self.config['password'].get()
        )

    def get_event(self, _id):
        """
        Get an event from an ID.

        :param _id: id of the event
        :type _id: str
        :rtype: :class:`BaseCalendarEvent` or None is fot found.
        """
        raise NotImplementedError()

    def list_events(self, date_from, date_to=None):
        return self.browser.all_events(date_from, date_to)

    def search_events(self, query):
        for ev in self.browser.all_events(query.start_date, query.end_date):
            if query.summary:
                if query.summary.lower() not in ev.summary.lower():
                    continue
            yield ev

    # TODO merge contiguous events?

    def iter_subscription(self):
        return self.browser.iter_subscriptions()

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)
        return self.browser.iter_documents(subscription)

    def get_document(self, id):
        subid = id.split('_')[0]
        return find_object(self.iter_documents(subid), id=id, error=DocumentNotFound)

    def download_document(self, document):
        return self.browser.open(document.url).content

    def iter_resources(self, objs, split_path):
        if Subscription in objs:
            return CapDocument.iter_resources(self, objs, split_path)
        return CapCalendarEvent.iter_resources(self, objs, split_path)

