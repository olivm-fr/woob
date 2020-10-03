# -*- coding: utf-8 -*-

# Copyright(C) 2017      Théo Dorée
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
from collections import OrderedDict

from weboob.capabilities.bill import DocumentTypes, CapDocument, Subscription, Document, SubscriptionNotFound, DocumentNotFound
from weboob.capabilities.base import find_object, NotAvailable
from weboob.tools.backend import Module, BackendConfig
from weboob.tools.compat import urljoin
from weboob.tools.value import ValueBackendPassword, Value, ValueTransient
from weboob.tools.pdf import html_to_pdf

from .browser import AmazonBrowser
from .en.browser import AmazonEnBrowser
from .de.browser import AmazonDeBrowser
from .uk.browser import AmazonUkBrowser

__all__ = ['AmazonModule']


class AmazonModule(Module, CapDocument):
    NAME = 'amazon'
    DESCRIPTION = 'Amazon'
    MAINTAINER = 'Théo Dorée'
    EMAIL = 'tdoree@budget-insight.com'
    LICENSE = 'LGPLv3+'
    VERSION = '2.1'

    website_choices = OrderedDict([(k, u'%s (%s)' % (v, k)) for k, v in sorted({
                        'www.amazon.com': u'Amazon.com',
                        'www.amazon.fr': u'Amazon France',
                        'www.amazon.de': u'Amazon.de',
                        'www.amazon.co.uk': u'Amazon UK',
                      }.items())])

    BROWSERS = {
        'www.amazon.fr': AmazonBrowser,
        'www.amazon.com': AmazonEnBrowser,
        'www.amazon.de': AmazonDeBrowser,
        'www.amazon.co.uk': AmazonUkBrowser,
    }

    CONFIG = BackendConfig(
        Value('website', label=u'Website', choices=website_choices, default='www.amazon.com'),
        ValueBackendPassword('email', label='Username', masked=False),
        ValueBackendPassword('password', label='Password'),
        Value('captcha_response', label='Captcha Response', required=False, default=''),
        Value('pin_code', label='OTP response', required=False, default=''),
        Value('request_information', label='request_information', default=None, required=False, noprompt=True),
        ValueTransient('resume'),
    )

    accepted_document_types = (DocumentTypes.BILL,)

    def create_default_browser(self):
        self.BROWSER = self.BROWSERS[self.config['website'].get()]
        return self.create_browser(self.config)

    def iter_subscription(self):
        return self.browser.iter_subscription()

    def get_subscription(self, _id):
        return find_object(self.iter_subscription(), id=_id, error=SubscriptionNotFound)

    def get_document(self, _id):
        subid = _id.rsplit('_', 1)[0]
        subscription = self.get_subscription(subid)

        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)
        return self.browser.iter_documents(subscription)

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        if document.url is NotAvailable:
            return

        return self.browser.open(document.url).content

    def download_document_pdf(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        if document.url is NotAvailable:
            return
        if document.format == 'pdf':
            return self.browser.open(document.url).content

        url = urljoin(self.browser.BASEURL, document.url)
        return html_to_pdf(self.browser, url=url)
