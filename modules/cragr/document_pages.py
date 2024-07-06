# Copyright(C) 2023 Powens
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

import re

from woob.browser.pages import LoggedPage, HTMLPage
from woob.capabilities.bill import Document, DocumentTypes, Subscription
from woob.browser.elements import ListElement, ItemElement, method
from woob.browser.filters.standard import Env, CleanText, Date, Regexp, Format
from woob.browser.filters.html import Link


class SubscriptionsTransitionPage(LoggedPage, HTMLPage):
    def submit(self, token):
        form = self.get_form(name='formulaire')
        form[':cq_csrf_token'] = token
        form['largeur_ecran'] = 1920
        form['hauteur_ecran'] = 1080
        form.submit()


class SubscriptionsDocumentsPage(LoggedPage, HTMLPage):
    def has_error(self):
        return bool(CleanText('//h1[contains(text(), "Erreur de téléchargement")]')(self.doc))

    @method
    class iter_subscription(ListElement):
        # Some subscriptions exist in 2 occurences in the page: e.g. one account has regular bank statement reports + deffered statements
        # there might be duplicate, but not a big deal woob is good and will keep only one subscription.
        ignore_duplicate = True
        item_xpath = '//div[contains(text(), "RELEVES DE COMPTES")]/following-sibling::table//tr//div[contains(@class, "table")]'

        class item(ItemElement):
            klass = Subscription

            def parse(self, el):
                raw = CleanText('./a')(self)
                # ex of account_name: CCHQ, LIV A, CEL2
                # ex of raw: CCOU 00000000000 MONSIEUR MICHU
                m = re.match(r'(.+) (\d{5,}) (.+)$', raw)
                assert m, 'Format of line is not: ACT 123456789 M. First Last'
                self.env['account_name'], self.env['account_id'], self.env['account_owner'] = m.groups()

            obj_label = Format('%s %s', Env('account_name'), Env('account_owner'))
            obj_subscriber = Env('account_owner')
            obj_id = Env('account_id')

    def get_document_page_urls(self, subscription):
        # each account can be displayed several times but with different set of documents
        # take all urls for each subscription
        xpath = '//div[contains(text(), "RELEVES DE COMPTES")]/following-sibling::table//tr//div[contains(@class, "table")]//a[contains(text(), "%s")]'

        # Declare a set for _urls to prevent the same URL from being added twice
        urls = set()
        for url in self.doc.xpath(xpath % subscription.id):
            urls.add(Link().filter([url]))

        return urls

    @method
    class iter_documents(ListElement):
        item_xpath = '//tr[@title="Relevé"][@id]'

        class item(ItemElement):
            klass = Document

            obj_id = Format('%s_%s', Env('sub_id'), Regexp(Link('./td/a'), r"mettreUnCookie\('(\d+)'"))
            obj_label = CleanText('./th/span')
            obj_date = Date(CleanText('td[1]'), dayfirst=True)
            obj_url = Regexp(Link('./td/a'), r"ouvreTelechargement\('(.*?)'\)")
            obj_type = DocumentTypes.STATEMENT
            obj_format = 'pdf'
