# -*- coding: utf-8 -*-

# Copyright(C) 2010-2011  Nicolas Duhamel
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

# flake8: compatible

from __future__ import unicode_literals

import re
from decimal import Decimal

from weboob.capabilities.base import NotAvailable
from weboob.capabilities.bank import Account, Loan, AccountOwnership
from weboob.capabilities.contact import Advisor
from weboob.capabilities.profile import Person
from weboob.browser.elements import ListElement, ItemElement, method, TableElement
from weboob.browser.pages import LoggedPage, RawPage, PartialHTMLPage, HTMLPage
from weboob.browser.filters.html import Link, TableCell, Attr
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Regexp, Env, Field, Currency,
    Async, Date, Format, Coalesce,
)
from weboob.exceptions import BrowserUnavailable
from weboob.tools.compat import urljoin, unicode
from weboob.tools.pdf import extract_text

from .base import MyHTMLPage


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True, default=NotAvailable)
    return CleanDecimal(*args, **kwargs)


def MyDate(*args, **kwargs):
    kwargs.update(dayfirst=True, default=NotAvailable)
    return Date(*args, **kwargs)


class item_account_generic(ItemElement):
    klass = Account

    def condition(self):
        # For some loans the following xpath is absent and we don't want to skip them
        # Also a case of loan that is empty and has no information exists and will be ignored
        return (
            len(self.el.xpath('.//span[@class="number"]')) > 0
            or (
                Field('type')(self) == Account.TYPE_LOAN
                and (
                    not bool(self.el.xpath('.//div//*[contains(text(),"pas la restitution de ces données.")]'))
                    and not bool(self.el.xpath('.//div[@class="amount"]/span[contains(text(), "Contrat résilié")]'))
                    and not bool(self.el.xpath('.//div[@class="amount"]/span[contains(text(), "Remboursé intégralement")]'))
                    and not bool(self.el.xpath('.//div[@class="amount"]/span[contains(text(), "Prêt non débloqué")]'))
                )
            )
        )

    obj_id = obj_number = CleanText('.//abbr/following-sibling::text()')
    obj_currency = Coalesce(Currency('.//span[@class="number"]'), Currency('.//span[@class="thick"]'))

    def obj_url(self):
        url = Link('./a', default=NotAvailable)(self)
        if not url:
            url = Regexp(Attr('.//span', 'onclick', default=''), r'\'(https.*)\'', default=NotAvailable)(self)
        if url:
            if 'CreditRenouvelable' in url:
                url = Link(u'.//a[contains(text(), "espace de gestion crédit renouvelable")]')(self.el)
            return urljoin(self.page.url, url)
        return url

    def obj_label(self):
        return CleanText('.//div[@class="title"]/h3')(self).upper()

    def obj_ownership(self):
        account_holder = CleanText('.//div[@class="title"]/span')(self)
        pattern = re.compile(
            r'(m|mr|me|mme|mlle|mle|ml)\.? (.*)\bou ?(m|mr|me|mme|mlle|mle|ml)?\b(.*)',
            re.IGNORECASE
        )
        if pattern.search(account_holder):
            return AccountOwnership.CO_OWNER
        elif all(n in account_holder for n in self.env['name'].split(' ')):
            return AccountOwnership.OWNER
        else:
            return AccountOwnership.ATTORNEY

    def obj_balance(self):
        if Field('type')(self) == Account.TYPE_LOAN:
            balance = CleanDecimal('.//span[@class="number"]', replace_dots=True, default=NotAvailable)(self)
            if balance:
                balance = -abs(balance)
            return balance
        return CleanDecimal('.//span[@class="number"]', replace_dots=True, default=NotAvailable)(self)

    def obj_coming(self):
        if Field('type')(self) == Account.TYPE_CHECKING and Field('balance')(self) != 0:
            # When the balance is 0, we get a website unavailable on the history page
            # and the following navigation is broken
            has_coming = False
            coming = 0

            details_page = self.page.browser.open(Field('url')(self))
            coming_op_link = Link(
                '//a[contains(text(), "Opérations à venir")]',
                default=NotAvailable
            )(details_page.page.doc)

            if coming_op_link:
                coming_op_link = Regexp(
                    Link('//a[contains(text(), "Opérations à venir")]'),
                    r'../(.*)'
                )(details_page.page.doc)

                coming_operations = self.page.browser.open(
                    self.page.browser.BASEURL + '/voscomptes/canalXHTML/CCP/' + coming_op_link
                )
            else:
                coming_op_link = Link('//a[contains(text(), "Opérations en cours")]')(details_page.page.doc)
                coming_operations = self.page.browser.open(coming_op_link)

            if CleanText('//span[@id="amount_total"]')(coming_operations.page.doc):
                has_coming = True
                coming += CleanDecimal('//span[@id="amount_total"]', replace_dots=True)(coming_operations.page.doc)

            if CleanText('.//dt[contains(., "Débit différé à débiter")]')(self):
                has_coming = True
                coming += CleanDecimal(
                    './/dt[contains(., "Débit différé à débiter")]/following-sibling::dd[1]',
                    replace_dots=True
                )(self)

            if has_coming:
                return coming

        return NotAvailable

    def obj_iban(self):
        if not Field('url')(self):
            return NotAvailable
        if Field('type')(self) not in (Account.TYPE_CHECKING, Account.TYPE_SAVINGS):
            return NotAvailable

        details_page = self.page.browser.open(Field('url')(self)).page
        rib_link = Link('//a[abbr[contains(text(), "RIB")]]', default=NotAvailable)(details_page.doc)
        if rib_link:
            response = self.page.browser.open(rib_link)
            return response.page.get_iban()

        elif Field('type')(self) == Account.TYPE_SAVINGS:
            # The rib link is available on the history page (ex: Livret A)
            his_page = self.page.browser.open(Field('url')(self))
            rib_link = Link('//a[abbr[contains(text(), "RIB")]]', default=NotAvailable)(his_page.page.doc)
            if rib_link:
                response = self.page.browser.open(rib_link)
                return response.page.get_iban()
        return NotAvailable

    def obj_type(self):
        types = {
            'comptes? bancaires?': Account.TYPE_CHECKING,
            "plan d'epargne populaire": Account.TYPE_SAVINGS,
            'livrets?': Account.TYPE_SAVINGS,
            'epargnes? logement': Account.TYPE_SAVINGS,
            "autres produits d'epargne": Account.TYPE_SAVINGS,
            'compte relais': Account.TYPE_SAVINGS,
            'comptes? titres? et pea': Account.TYPE_MARKET,
            'compte-titres': Account.TYPE_MARKET,
            'assurances? vie': Account.TYPE_LIFE_INSURANCE,
            'prêt': Account.TYPE_LOAN,
            'crédits?': Account.TYPE_LOAN,
            "plan d'epargne en actions": Account.TYPE_PEA,
            'comptes? attente': Account.TYPE_CHECKING,
            'perp': Account.TYPE_PERP,
            'assurances? retraite': Account.TYPE_PERP,
        }

        # first trying to match with label
        label = Field('label')(self)
        for atypetxt, atype in types.items():
            if re.findall(atypetxt, label.lower()):  # match with/without plurial in type
                return atype
        # then by type
        type = Regexp(CleanText('../../preceding-sibling::div[@class="avoirs"][1]/span[1]'), r'(\d+) (.*)', '\\2')(self)
        for atypetxt, atype in types.items():
            if re.findall(atypetxt, type.lower()):  # match with/without plurial in type
                return atype

        return Account.TYPE_UNKNOWN

    def obj__has_cards(self):
        return Link('.//a[contains(@href, "consultationCarte")]', default=None)(self)


class AccountList(LoggedPage, MyHTMLPage):
    def on_load(self):
        MyHTMLPage.on_load(self)

        # website sometimes crash
        if CleanText('//h2[text()="ERREUR"]')(self.doc):
            self.browser.location('https://voscomptesenligne.labanquepostale.fr/voscomptes/canalXHTML/securite/authentification/initialiser-identif.ea')

            raise BrowserUnavailable()

    def go_revolving(self):
        form = self.get_form()
        form.submit()

    @property
    def no_accounts(self):
        return (
            len(
                self.doc.xpath("""
                    //iframe[contains(@src, "/comptes_contrats/sans_")]
                    | //iframe[contains(@src, "bel_particuliers/prets/prets_nonclient")]
                """)
            ) > 0
        )

    @property
    def has_mandate_management_space(self):
        return len(self.doc.xpath('//a[@title="Accéder aux Comptes Gérés Sous Mandat"]')) > 0

    def mandate_management_space_link(self):
        return Link('//a[@title="Accéder aux Comptes Gérés Sous Mandat"]')(self.doc)

    @method
    class iter_accounts(ListElement):
        item_xpath = '//ul/li//div[contains(@class, "account-resume")]'

        class item_account(item_account_generic):
            def condition(self):
                return item_account_generic.condition(self)

    @method
    class iter_revolving_loans(ListElement):
        item_xpath = '//div[@class="bloc Tmargin"]//dl'

        class item_account(ItemElement):
            klass = Loan

            obj_id = CleanText('./dd[1]//em')
            obj_label = 'Crédit renouvelable'
            obj_total_amount = MyDecimal('./dd[2]/span')
            obj_used_amount = MyDecimal('./dd[3]/span')
            obj_available_amount = MyDecimal('./dd[4]//em')
            obj_insurance_label = CleanText('./dd[5]//em', children=False)
            obj__has_cards = False
            obj_type = Account.TYPE_LOAN

            def obj_url(self):
                return self.page.url

    @method
    class iter_loans(TableElement):
        head_xpath = '//table[@id="pret" or @class="dataNum"]/thead//th'
        item_xpath = '//table[@id="pret"]/tbody/tr'

        col_label = ('Numéro du prêt', "Numéro de l'offre")
        col_total_amount = 'Montant initial emprunté'
        col_subscription_date = 'MONTANT INITIAL EMPRUNTÉ'
        col_next_payment_amount = 'Montant prochaine échéance'
        col_next_payment_date = 'Date prochaine échéance'
        col_balance = re.compile('Capital')
        col_maturity_date = re.compile(u'Date dernière')

        class item_loans(ItemElement):
            # there is two cases : the mortgage and the consumption loan. These cases have differents way to get the details
            # except for student loans, you may have 1 or 2 tables to deal with

            # if 1 table, item_loans is used for student loan
            # if 2 tables, get_student_loan is used
            klass = Loan

            def condition(self):
                if CleanText(TableCell('balance'))(self) != 'Prêt non débloqué':
                    return bool(not self.xpath('//caption[contains(text(), "Période de franchise du")]'))
                return CleanText(TableCell('balance'))(self) != 'Prêt non débloqué'

            def load_details(self):
                url = Link('.//a', default=NotAvailable)(self)
                return self.page.browser.async_open(url=url)

            obj_total_amount = CleanDecimal(TableCell('total_amount'), replace_dots=True, default=NotAvailable)

            def obj_id(self):
                if TableCell('label', default=None)(self):
                    return Regexp(CleanText(Field('label'), default=NotAvailable), r'- (\w{16})')(self)

                # student_loan
                if CleanText('//select[@id="numOffrePretSelection"]/option[@selected="selected"]')(self):
                    return Regexp(
                        CleanText(
                            '//select[@id="numOffrePretSelection"]/option[@selected="selected"]'
                        ),
                        r'(\d+)'
                    )(self)

                return CleanText(
                    '//form[contains(@action, "detaillerOffre") or contains(@action, "detaillerPretPartenaireListe-encoursPrets.ea")]/div[@class="bloc Tmargin"]/div[@class="formline"][2]/span/strong'
                )(self)

            obj_type = Account.TYPE_LOAN

            def obj_label(self):
                cell = TableCell('label', default=None)(self)
                if cell:
                    return CleanText(cell, default=NotAvailable)(self).upper()

                return CleanText(
                    '//form[contains(@action, "detaillerOffre") or contains(@action, "detaillerPretPartenaireListe-encoursPrets.ea")]/div[@class="bloc Tmargin"]/h2[@class="title-level2"]'
                )(self).upper()

            def obj_balance(self):
                if CleanText(TableCell('balance'))(self) != u'Remboursé intégralement':
                    return -abs(CleanDecimal(TableCell('balance'), replace_dots=True)(self))
                return Decimal(0)

            def obj_subscription_date(self):
                xpath = '//form[contains(@action, "detaillerOffre")]/div[1]/div[2]/span'
                if 'souscrite le' in CleanText(xpath)(self):
                    return MyDate(
                        Regexp(
                            CleanText(xpath),
                            r' (\d{2}/\d{2}/\d{4})',
                            default=NotAvailable
                        )
                    )(self)

                return NotAvailable

            obj_next_payment_amount = CleanDecimal(
                TableCell('next_payment_amount'),
                replace_dots=True,
                default=NotAvailable
            )

            def obj_maturity_date(self):
                if Field('subscription_date')(self):
                    async_page = Async('details').loaded_page(self)
                    return MyDate(
                        CleanText('//div[@class="bloc Tmargin"]/dl[2]/dd[4]')
                    )(async_page.doc)

                return MyDate(CleanText(TableCell('maturity_date')), default=NotAvailable)(self)

            def obj_last_payment_date(self):
                xpath = '//div[@class="bloc Tmargin"]/div[@class="formline"][2]/span'
                if 'dont le dernier' in CleanText(xpath)(self):
                    return MyDate(
                        Regexp(
                            CleanText(xpath),
                            r' (\d{2}/\d{2}/\d{4})',
                            default=NotAvailable
                        )
                    )(self)

                async_page = Async('details').loaded_page(self)
                return MyDate(
                    CleanText(
                        '//div[@class="bloc Tmargin"]/dl[1]/dd[2]'
                    ),
                    default=NotAvailable
                )(async_page.doc)

            obj_next_payment_date = MyDate(CleanText(TableCell('next_payment_date')), default=NotAvailable)

            def obj_url(self):
                url = Link('.//a', default=None)(self)
                if url:
                    return urljoin(self.page.url, url)
                return self.page.url

            obj__has_cards = False

    @method
    class get_student_loan(ItemElement):
        # 2 tables student loan
        klass = Loan

        def condition(self):
            return bool(self.xpath('//caption[contains(text(), "Période de franchise du")]'))

        # get all table headers
        def obj__heads(self):
            heads_xpath = '//table[@class="dataNum"]/thead//th'
            return [CleanText('.')(head) for head in self.xpath(heads_xpath)]

        # get all table elements
        def obj__items(self):
            items_xpath = '//table[@class="dataNum"]/tbody//td'
            return [CleanText('.')(item) for item in self.xpath(items_xpath)]

        def get_element(self, header_name):
            for index, head in enumerate(Field('_heads')(self)):
                if header_name in head:
                    return Field('_items')(self)[index]
            raise AssertionError()

        obj_id = Regexp(CleanText('//select[@id="numOffrePretSelection"]/option[@selected="selected"]'), r'(\d+)')
        obj_type = Account.TYPE_LOAN
        obj__has_cards = False

        def obj_total_amount(self):
            return CleanDecimal(replace_dots=True).filter(self.get_element('Montant initial'))

        def obj_label(self):
            return Regexp(CleanText('//h2[@class="title-level2"]'), r'([\w ]+)', flags=re.U)(self)

        def obj_balance(self):
            return -CleanDecimal(replace_dots=True).filter(self.get_element('Capital restant'))

        def obj_maturity_date(self):
            return Date(dayfirst=True).filter(self.get_element('Date derni'))

        def obj_duration(self):
            return CleanDecimal().filter(self.get_element("de l'amortissement"))

        def obj_next_payment_date(self):
            return Date(dayfirst=True).filter(self.get_element('Date de prochaine'))

        def obj_next_payment_amount(self):
            if 'Donnée non disponible' in CleanText().filter(self.get_element('Montant prochaine')):
                return NotAvailable
            return CleanDecimal(replace_dots=True).filter(self.get_element('Montant prochaine'))

        def obj_url(self):
            return self.page.url


class Advisor(LoggedPage, MyHTMLPage):
    @method
    class get_advisor(ItemElement):
        klass = Advisor

        obj_name = Env('name')
        obj_phone = Env('phone')
        obj_mobile = Env('mobile', default=NotAvailable)
        obj_agency = Env('agency', default=NotAvailable)
        obj_email = NotAvailable

        def obj_address(self):
            return (
                CleanText('//div[h3[contains(text(), "Bureau")]]/div[not(@class)][position() > 1]')(self)
                or NotAvailable
            )

        def parse(self, el):
            # we have two kinds of page and sometimes we don't have any advisor
            agency_phone = (
                CleanText('//span/a[contains(@href, "rendezVous")]', replace=[(' ', '')], default=NotAvailable)(self)
                or CleanText('//div[has-class("lbp-numero")]/span', replace=[(' ', '')], default=NotAvailable)(self)
            )

            advisor_phone = Regexp(
                CleanText('//div[h3[contains(text(), "conseil")]]//span[2]', replace=[(' ', '')]),
                r'(\d+)',
                default=""
            )(self)
            if advisor_phone.startswith(("06", "07")):
                self.env['phone'] = agency_phone
                self.env['mobile'] = advisor_phone
            else:
                self.env['phone'] = advisor_phone or agency_phone

            agency = CleanText('//div[h3[contains(text(), "Bureau")]]/div[not(@class)][1]')(self) or NotAvailable
            name = (
                CleanText('//div[h3[contains(text(), "conseil")]]//span[1]', default=None)(self)
                or CleanText('//div[@class="lbp-font-accueil"]/div[2]/div[1]/span[1]', default=None)(self)
            )
            if name:
                self.env['name'] = name
                self.env['agency'] = agency
            else:
                self.env['name'] = agency


class AccountRIB(LoggedPage, RawPage):
    iban_regexp = r'[A-Z]{2}\d{12}[0-9A-Z]{11}\d{2}'

    def get_iban(self):
        m = re.search(self.iban_regexp, extract_text(self.data))
        if m:
            return unicode(m.group(0))
        return None


class MarketHomePage(LoggedPage, HTMLPage):
    pass


class MarketLoginPage(LoggedPage, PartialHTMLPage):
    def on_load(self):
        self.get_form(id='autoSubmit').submit()


class MarketCheckPage(LoggedPage, HTMLPage):
    def on_load(self):
        self.browser.market_login.go()


class UselessPage(LoggedPage, RawPage):
    pass


class ProfilePage(LoggedPage, HTMLPage):
    def get_profile(self):
        profile = Person()

        profile.name = Format(
            '%s %s',
            CleanText('//div[@id="persoIdentiteDetail"]//dd[3]'),
            CleanText('//div[@id="persoIdentiteDetail"]//dd[2]')
        )(self.doc)
        profile.address = CleanText('//div[@id="persoAdresseDetail"]//dd')(self.doc)
        profile.email = CleanText('//div[@id="persoEmailDetail"]//td[2]')(self.doc)
        profile.job = CleanText('//div[@id="persoIdentiteDetail"]//dd[4]')(self.doc)

        return profile


class RevolvingAttributesPage(LoggedPage, HTMLPage):
    def on_load(self):
        if CleanText('//h1[contains(text(), "Erreur")]')(self.doc):
            raise BrowserUnavailable()

    def get_revolving_attributes(self, account):
        loan = Loan()
        loan.id = account.id
        loan.label = '%s - %s' % (account.label, account.id)
        loan.currency = account.currency
        loan.url = account.url

        loan.used_amount = CleanDecimal.US(
            '//tr[td[contains(text(), "Montant Utilisé") or contains(text(), "Montant utilisé")]]/td[2]'
        )(self.doc)

        loan.available_amount = CleanDecimal.US(
            Regexp(
                CleanText('//tr[td[contains(text(), "Montant Disponible") or contains(text(), "Montant disponible")]]/td[2]'),
                r'(.*) au'
            )
        )(self.doc)

        loan.balance = -loan.used_amount
        loan._has_cards = False
        loan.type = Account.TYPE_REVOLVING_CREDIT
        return loan
