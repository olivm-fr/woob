# Copyright(C) 2012-2023  Powens
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

from woob.tools.backend import BackendConfig
from woob.tools.value import ValueBackendPassword, Value
from woob.capabilities.base import find_object, NotAvailable
from woob.capabilities.bill import (
    DocumentTypes, CapDocument, Subscription, Document, DocumentNotFound,
    DocumentCategory,
)
from woob.capabilities.profile import CapProfile
from woob_modules.franceconnect.module import FranceConnectModule

from .browser import ImpotsParBrowser


__all__ = ['ImpotsGouvFrParModule']


class ImpotsGouvFrParModule(FranceConnectModule, CapDocument, CapProfile):
    NAME = 'impotsgouvfrpar'
    DESCRIPTION = 'Impots - Avis et declarations - particulier'
    MAINTAINER = 'Florian Duguet'
    EMAIL = 'florian.duguet@budget-insight.com'
    LICENSE = 'LGPLv3+'
    VERSION = '3.6'
    DEPENDENCIES = ('franceconnect',)
    CONFIG = BackendConfig(
        ValueBackendPassword('login', label="Identifiant (dépend de votre méthode d'authentification)"),
        Value(
            'login_source', label="méthode d'authentification", default='direct',
            choices={
                'direct': 'directe',
                'fc': 'France Connect impots',
                'fc_ameli': 'France connect Ameli',
            }
        ),
        ValueBackendPassword('password', label='Mot de passe'),
    )

    BROWSER = ImpotsParBrowser

    accepted_document_types = (DocumentTypes.NOTICE, DocumentTypes.OTHER)
    document_categories = {DocumentCategory.TAX}

    def create_default_browser(self):
        return self.create_browser(
            self.config['login_source'].get(),
            self.config['login'].get(),
            self.config['password'].get(),
        )

    def iter_subscription(self):
        return self.browser.iter_subscription()

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

    def get_profile(self):
        return self.browser.get_profile()
