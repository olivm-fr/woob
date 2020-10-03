# -*- coding: utf-8 -*-

# Copyright(C) 2012-2013 Romain Bignon
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

from weboob.capabilities.bank import Account, AccountNotFound
from weboob.capabilities.wealth import CapBankWealth
from weboob.capabilities.base import find_object
from weboob.capabilities.bill import (
    CapDocument, SubscriptionNotFound, DocumentNotFound, Document, Subscription, DocumentTypes,
)
from weboob.capabilities.contact import CapContact
from weboob.capabilities.profile import CapProfile
from weboob.tools.backend import Module, BackendConfig
from weboob.tools.value import ValueBackendPassword, Value

from .browser import BanquePopulaire


__all__ = ['BanquePopulaireModule']


class BanquePopulaireModule(Module, CapBankWealth, CapContact, CapProfile, CapDocument):
    NAME = 'banquepopulaire'
    MAINTAINER = 'Romain Bignon'
    EMAIL = 'romain@weboob.org'
    VERSION = '2.1'
    DESCRIPTION = 'Banque Populaire'
    LICENSE = 'LGPLv3+'
    website_choices = OrderedDict([(k, '%s (%s)' % (v, k)) for k, v in sorted({
        'www.ibps.alpes.banquepopulaire.fr': 'Alpes',
        'www.ibps.alsace.banquepopulaire.fr': 'Alsace Lorraine Champagne',
        'www.ibps.bpalc.banquepopulaire.fr' : 'Alsace Lorraine Champagne',
        'www.ibps.bpaca.banquepopulaire.fr': 'Aquitaine Centre atlantique',
        'www.ibps.atlantique.banquepopulaire.fr': 'Atlantique',
        'www.ibps.bpgo.banquepopulaire.fr': 'Grand Ouest',
        'www.ibps.loirelyonnais.banquepopulaire.fr': 'Auvergne Rhône Alpes',
        'www.ibps.bpaura.banquepopulaire.fr': 'Auvergne Rhône Alpes',
        'www.ibps.banquedesavoie.banquepopulaire.fr': 'Banque de Savoie',
        'www.ibps.bpbfc.banquepopulaire.fr': 'Bourgogne Franche-Comté',
        'www.ibps.bretagnenormandie.cmm.groupe.banquepopulaire.fr': 'Crédit Maritime Bretagne Normandie',
        'www.ibps.atlantique.creditmaritime.groupe.banquepopulaire.fr': 'Crédit Maritime Atlantique',
        'www.ibps.sudouest.creditmaritime.groupe.banquepopulaire.fr': 'Crédit Maritime du Littoral du Sud-Ouest',
        'www.ibps.lorrainechampagne.banquepopulaire.fr': 'Lorraine Champagne',
        'www.ibps.massifcentral.banquepopulaire.fr': 'Massif central',
        'www.ibps.mediterranee.banquepopulaire.fr': 'Méditerranée',
        'www.ibps.nord.banquepopulaire.fr': 'Nord',
        'www.ibps.occitane.banquepopulaire.fr': 'Occitane',
        'www.ibps.ouest.banquepopulaire.fr': 'Ouest',
        'www.ibps.rivesparis.banquepopulaire.fr': 'Rives de Paris',
        'www.ibps.sud.banquepopulaire.fr': 'Sud',
        'www.ibps.valdefrance.banquepopulaire.fr': 'Val de France',
        }.items(), key=lambda k_v: (k_v[1], k_v[0]))])

    # Some regions have been renamed after bank cooptation
    region_aliases = {
        'www.ibps.alsace.banquepopulaire.fr': 'www.ibps.bpalc.banquepopulaire.fr',
        'www.ibps.lorrainechampagne.banquepopulaire.fr': 'www.ibps.bpalc.banquepopulaire.fr',
        'www.ibps.loirelyonnais.banquepopulaire.fr': 'www.ibps.bpaura.banquepopulaire.fr',
        'www.ibps.alpes.banquepopulaire.fr': 'www.ibps.bpaura.banquepopulaire.fr',
        'www.ibps.massifcentral.banquepopulaire.fr': 'www.ibps.bpaura.banquepopulaire.fr',
        # creditmaritime atlantique now redirecting to Banque Populaire Aquitaine Centre Atlantique (new website)
        'www.ibps.atlantique.creditmaritime.groupe.banquepopulaire.fr': 'www.ibps.bpaca.banquepopulaire.fr',
        # creditmaritime sudouest now redirecting to Banque Populaire Aquitaine Centre Atlantique (new website)
        'www.ibps.sudouest.creditmaritime.groupe.banquepopulaire.fr': 'www.ibps.bpaca.banquepopulaire.fr',
        # creditmaritime bretagnenormandie now redirecting to Banque Populaire Grand Ouest (old website)
        'www.ibps.bretagnenormandie.cmm.groupe.banquepopulaire.fr': 'www.ibps.cmgo.creditmaritime.groupe.banquepopulaire.fr',
        'www.ibps.atlantique.banquepopulaire.fr': 'www.ibps.bpgo.banquepopulaire.fr',
        'www.ibps.ouest.banquepopulaire.fr': 'www.ibps.bpgo.banquepopulaire.fr',
    }

    CONFIG = BackendConfig(
        Value('website', label='Région', choices=website_choices, aliases=region_aliases),
        ValueBackendPassword('login', label='Identifiant', masked=False, regexp=r'[a-zA-Z0-9]+'),
        ValueBackendPassword('password', label='Mot de passe')
    )

    BROWSER = BanquePopulaire

    accepted_document_types = (DocumentTypes.STATEMENT,)

    def create_default_browser(self):
        website = self.config['website'].get()

        return self.create_browser(
            website,
            self.config['login'].get(),
            self.config['password'].get(),
            weboob=self.weboob
        )

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def get_account(self, _id):
        account = self.browser.get_account(_id)
        if account:
            return account
        else:
            raise AccountNotFound()

    def iter_history(self, account):
        return self.browser.iter_history(account)

    def iter_coming(self, account):
        return self.browser.iter_history(account, coming=True)

    def iter_investment(self, account):
        return self.browser.iter_investments(account)

    def iter_market_orders(self, account):
        return self.browser.iter_market_orders(account)

    def iter_contacts(self):
        return self.browser.get_advisor()

    def get_profile(self):
        return self.browser.get_profile()

    def iter_subscription(self):
        return self.browser.iter_subscriptions()

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)
        return self.browser.iter_documents(subscription)

    def get_subscription(self, _id):
        return find_object(self.iter_subscription(), id=_id, error=SubscriptionNotFound)

    def get_document(self, _id):
        subid = _id.rsplit('_', 1)[0]
        subscription = self.get_subscription(subid)

        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)

        return self.browser.download_document(document)

    def iter_resources(self, objs, split_path):
        if Account in objs:
            self._restrict_level(split_path)
            return self.iter_accounts()
        if Subscription in objs:
            self._restrict_level(split_path)
            return self.iter_subscription()
