# -*- coding: utf-8 -*-

# Copyright(C) 2015      Baptiste Delpey
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

from base64 import b64decode
from collections import OrderedDict
import datetime
from io import BytesIO
import re

from PIL import Image

from weboob.exceptions import ActionNeeded
from weboob.browser.pages import LoggedPage, HTMLPage, pagination, AbstractPage, JsonPage
from weboob.browser.elements import method, ListElement, ItemElement, TableElement
from weboob.capabilities.bank import Account, AccountOwnership
from weboob.capabilities.profile import Person
from weboob.browser.filters.html import Link, Attr, TableCell
from weboob.browser.filters.standard import (
    CleanText, Regexp, Field, Map, CleanDecimal, Date, Format,
)
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.compat import urlencode, urlparse, urlunparse, parse_qsl, urljoin


class BfBKeyboard(object):
    symbols = {
        '0': '00111111001111111111111111111111000000111000000001111111111111111111110011111100',
        '1': '00000000000011000000011100000001100000001111111111111111111100000000000000000000',
        '2': '00100000111110000111111000011111000011111000011101111111100111111100010111000001',
        '3': '00100001001110000111111000011111001000011101100001111111011111111111110000011110',
        '4': '00000011000000111100000111010001111001001111111111111111111111111111110000000100',
        '5': '00000001001111100111111110011110010000011001000001100110011110011111110000111110',
        '6': '00011111000111111110111111111111001100011000100001110011001111001111110100011110',
        '7': '10000000001000000000100000111110011111111011111100111110000011100000001100000000',
        '8': '00000011001111111111111111111110001000011000100001111111111111111111110010011110',
        '9': '00111000001111110011111111001110000100011000010011111111111111111111110011111100',
    }

    def __init__(self, basepage):
        self.basepage = basepage
        self.fingerprints = []
        for htmlimg in self.basepage.doc.xpath('.//div[@class="m-btn-pin"]//img'):
            url = htmlimg.attrib.get("src")
            imgfile = BytesIO(b64decode(re.match('data:image/png;base64,(.*)', url).group(1)))
            img = Image.open(imgfile)
            matrix = img.load()
            s = ""
            # The digit is only displayed in the center of image
            for x in range(19, 27):
                for y in range(17, 27):
                    (r, g, b, o) = matrix[x, y]
                    # If the pixel is "red" enough
                    if g + b < 450:
                        s += "1"
                    else:
                        s += "0"

            self.fingerprints.append(s)

    def get_symbol_code(self, digit):
        fingerprint = self.symbols[digit]
        for i, string in enumerate(self.fingerprints):
            if string == fingerprint:
                return i

    def get_string_code(self, string):
        code = ''
        for c in string:
            codesymbol = self.get_symbol_code(c)
            code += str(codesymbol)
        return code


class SendTwoFAPage(JsonPage):
    pass


class LoginPage(HTMLPage):
    def get_pinpad_id(self):
        return Attr('//input[@id="pinpadId"]', 'value')(self.doc)


class ErrorPage(JsonPage):
    def get_error_message(self):
        return self.doc.get('errorMessage', None)


class UserValidationPage(HTMLPage):
    pass


class MyDecimal(CleanDecimal):
    # BforBank uses commas for thousands seps et and decimal seps
    def filter(self, text):
        text = super(CleanDecimal, self).filter(text)
        text = re.sub(r'[^\d\-\,]', '', text)
        text = re.sub(r',(?!(\d+$))', '', text)
        return super(MyDecimal, self).filter(text)


class RibPage(LoggedPage, HTMLPage):
    def populate_rib(self, accounts):
        for option in self.doc.xpath('//select[@id="compte-select"]/option'):
            if 'selected' in option.attrib:
                self.get_iban(accounts)
            else:
                page = self.browser.rib.go(id=re.sub(r'[^\d]', '', Attr('.', 'value')(option)))
                page.get_iban(accounts)

    def get_iban(self, accounts):
        for account in accounts:
            if self.doc.xpath('//option[@selected and contains(@value, $id)]', id=account.id):
                account.iban = CleanText(
                    '//td[contains(text(), "IBAN")]/following-sibling::td[1]',
                    replace=[(' ', '')]
                )(self.doc)


class AccountsPage(LoggedPage, HTMLPage):
    RIB_AVAILABLE = True

    def on_load(self):
        if not self.doc.xpath('//span[@class="title" and contains(text(), "RIB")]'):
            self.RIB_AVAILABLE = False

    @method
    class iter_accounts(ListElement):
        item_xpath = '//table/tbody/tr'

        class item(ItemElement):
            klass = Account

            TYPE = {
                'Livret': Account.TYPE_SAVINGS,
                'Compte': Account.TYPE_CHECKING,
                'PEA': Account.TYPE_PEA,
                'PEA-PME': Account.TYPE_PEA,
                'Compte-titres': Account.TYPE_MARKET,
                'Assurance-vie': Account.TYPE_LIFE_INSURANCE,
                'Crédit': Account.TYPE_LOAN,
            }

            obj_id = Regexp(
                CleanText('./td//div[contains(@class, "-synthese-title") or contains(@class, "-synthese-text")]'),
                r'(\d+)'
            )
            obj_number = obj_id
            obj_label = CleanText('./td//div[contains(@class, "-synthese-title")]')
            obj_balance = MyDecimal('./td//div[contains(@class, "-synthese-num")]', replace_dots=True)
            obj_currency = FrenchTransaction.Currency('./td//div[contains(@class, "-synthese-num")]')
            obj_type = Map(Regexp(Field('label'), r'^([^ ]*)'), TYPE, default=Account.TYPE_UNKNOWN)

            def obj_url(self):
                return urljoin(self.page.url, CleanText('./@data-href')(self))

            obj__card_balance = CleanDecimal('./td//div[@class="synthese-encours"][last()]/div[2]', default=None)

            def condition(self):
                return not len(self.el.xpath('./td[@class="chart"]'))

            owner_re = re.compile(
                r'(m|mr|me|mme|mlle|mle|ml)\.? (.*)\bou (m|mr|me|mme|mlle|mle|ml)\b(.*)',
                re.IGNORECASE
            )

            def obj_ownership(self):
                owner = CleanText(
                    './td//div[contains(@class, "-synthese-text") and not(starts-with(., "N°"))]',
                    default=None
                )(self)

                if owner:
                    if self.owner_re.search(owner):
                        return AccountOwnership.CO_OWNER
                    elif all(n in owner.upper() for n in self.env['name'].split()):
                        return AccountOwnership.OWNER
                    return AccountOwnership.ATTORNEY


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile('^(?P<category>VIREMENT)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile('^(?P<category>INTERETS)'), FrenchTransaction.TYPE_BANK),
        (re.compile('^RETRAIT AU DISTRIBUTEUR'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile('^Règlement cartes à débit différé du'), FrenchTransaction.TYPE_CARD_SUMMARY),
    ]


class LoanHistoryPage(LoggedPage, HTMLPage):
    @method
    class get_operations(ListElement):
        item_xpath = '//table[contains(@class, "table")]/tbody/div/tr[contains(@class, "submit")]'

        class item(ItemElement):
            klass = Transaction

            obj_amount = MyDecimal('./td[4]', replace_dots=True)
            obj_date = Transaction.Date('./td[2]')
            obj_vdate = Transaction.Date('./td[3]')
            obj_raw = Transaction.Raw(Format('%s %s', CleanText('./td[1]'), CleanText('./following-sibling::tr[contains(@class, "tr-more")]/td/p[1]/span')))


class HistoryPage(LoggedPage, HTMLPage):
    @pagination
    @method
    class get_operations(ListElement):
        item_xpath = '//table[has-class("style-operations")]/tbody//tr'
        next_page = Link('//div[@class="m-table-paginator full-width-xs"]//a[@id="next-page"]')

        class item(ItemElement):
            klass = Transaction

            def condition(self):
                if 'tr-section' in self.el.attrib.get('class', ''):
                    return False
                elif 'tr-trigger' in self.el.attrib.get('class', ''):
                    return True

                return False

            def obj_date(self):
                return Transaction.Date(
                    Regexp(
                        CleanText('./preceding::tr[has-class("tr-section")][1]/th'),
                        r'(\d+/\d+/\d+)'
                    )
                )(self)

            obj_raw = Transaction.Raw(Format('%s %s', CleanText('./td[1]'), CleanText('./following-sibling::tr[contains(@class, "tr-more")]/td/p[1]/span')))
            obj_amount = MyDecimal('./td[2]', replace_dots=True)

    @method
    class get_today_operations(TableElement):
        item_xpath = '//table[has-class("style-virements")]/tbody/tr[@class="tr-trigger"]'
        head_xpath = '//table[has-class("style-virements")]/thead/tr/th'

        col_amount = 'Montant'
        col_raw = 'Libellé'

        class item(ItemElement):
            klass = Transaction

            def obj_date(self):
                return datetime.date.today()

            obj_raw = Transaction.Raw(TableCell('raw'))
            obj_amount = MyDecimal(TableCell('amount'), replace_dots=True)


def add_qs(url, **kwargs):
    parts = list(urlparse(url))
    qs = OrderedDict(parse_qsl(parts[4]))
    qs.update(kwargs)
    parts[4] = urlencode(qs)
    return urlunparse(parts)


class CardHistoryPage(LoggedPage, HTMLPage):
    def get_card_indexes(self):
        for opt in self.doc.xpath('//select[@id="select-box-card"]/option'):
            number = CleanText('.')(opt).replace(' ', '').replace('*', 'x')
            number = re.search(r'\d{4}x+\d{4}', number).group(0)
            yield number, opt.attrib['value']

    def get_balance(self):
        div, = self.doc.xpath('//div[@class="m-tabs-tab-meta"]')
        for d in div.xpath('.//div[has-class("pull-left")]'):
            if 'opération(s):' in CleanText('.')(d):
                return MyDecimal('./span', replace_dots=True)(d)

    def get_debit_date(self):
        return (
            Date(
                Regexp(
                    CleanText('//div[@class="m-tabs-tab-meta"]'),
                    r'Ces opérations (?:seront|ont été) débitées sur votre compte le (\d{2}/\d{2}/\d{4})'),
                dayfirst=True
            )(self.doc)
        )

    def create_summary(self):
        tr = Transaction()
        tr.type = Transaction.TYPE_CARD_SUMMARY
        tr.amount = abs(self.get_balance())
        tr.label = 'Règlement cartes à débit différé'
        tr.date = tr.rdate = self.get_debit_date()
        return tr

    @pagination
    @method
    class get_operations(TableElement):
        head_xpath = '//table[has-class("style-operations")]//th'
        item_xpath = '//table[has-class("style-operations")]/tbody/tr[not(has-class("tr-category") or has-class("tr-more"))]'

        def next_page(self):
            page = Attr('//a[@id="next-page"]', 'data')(self)
            return add_qs(self.page.url, page=page)

        col_raw = 'Libellé'
        col_vdate = 'Date opération'
        col_amount = 'Montant'

        class item(ItemElement):
            klass = Transaction

            def condition(self):
                return CleanText('.')(self) != 'Aucune opération effectuée'

            obj_type = Transaction.TYPE_DEFERRED_CARD
            obj_raw = CleanText(TableCell('raw'))
            obj_vdate = obj_rdate = obj_bdate = Date(CleanText(TableCell('vdate')), dayfirst=True)
            obj_amount = MyDecimal(TableCell('amount'), replace_dots=True)

            def obj_date(self):
                return self.page.get_debit_date()


class CardPage(LoggedPage, HTMLPage):
    def has_no_card(self):
        # Persistent message for cardless accounts
        return (
            CleanText(
                '''//div[@id="alert"]/p[contains(text(), "Aucune donnée n'a été retournée par le service")]'''
            )(self.doc)
            or not self.doc.xpath('//div[@class="content-boxed"]')
        )

    def get_cards(self, account_id):
        divs = self.doc.xpath('//div[@class="content-boxed"]')
        msgs = re.compile(
            'Vous avez fait opposition sur cette carte bancaire.'
            + '|Votre carte bancaire a été envoyée.'
            + '|Carte bancaire commandée.'
            + '|BforBank a fait opposition sur votre carte'
            + '|Pour des raisons de sécurité, la demande de réception du code confidentiel de votre carte par SMS est indisponible'
        )
        divs = [d for d in divs if not msgs.search(CleanText('.//div[has-class("alert")]', default='')(d))]
        divs = [d.xpath('.//div[@class="m-card-infos"]')[0] for d in divs]
        divs = [d for d in divs if not d.xpath('.//div[@class="m-card-infos-body-text"][text()="Débit immédiat"]')]

        if not len(divs):
            self.logger.warning('all cards are cancelled, acting as if there is no card')
            return []

        cards = []
        for div in divs:
            label = CleanText('.//div[@class="m-card-infos-body-title"]')(div)
            number = CleanText('.//div[@class="m-card-infos-body-num"]', default='')(div)
            number = re.sub(r'[^\d*]', '', number).replace('*', 'x')
            debit = CleanText('.//div[@class="m-card-infos-body-text"][contains(text(),"Débit")]')(div)
            assert debit == 'Débit différé', 'unrecognized card type %s: %s' % (number, debit)

            card = Account()
            card.id = '%s.%s' % (account_id, number)
            card.label = label
            card.number = number
            card.type = Account.TYPE_CARD
            cards.append(card)

        return cards


class LifeInsuranceList(LoggedPage, HTMLPage):
    @method
    class iter_accounts(ListElement):
        item_xpath = '//table[has-class("comptes_liste")]/tbody//tr'

        class item(ItemElement):
            klass = Account

            obj_id = CleanText('./td/a')

            def obj_url(self):
                return urljoin(self.page.url, Link('./td/a')(self))


class LifeInsuranceIframe(LoggedPage, HTMLPage):
    def get_iframe(self):
        return Attr(None, 'src').filter(self.doc.xpath('//iframe[@id="iframePartenaire"]'))


class LifeInsuranceRedir(LoggedPage, HTMLPage):
    def get_redir(self):
        # meta http-equiv redirection...
        for meta in self.doc.xpath('//meta[@http-equiv="Refresh"]/@content'):
            match = re.search(r'URL=([^\s"\']+)', meta)
            if match:
                return match.group(1)


class BourseActionNeeded(LoggedPage, HTMLPage):
    ENCODING = 'latin-1'
    XPATH = "//div[contains(text(), 'Création ou modification de votre mot de passe trading')]"

    def is_here(self):
        return CleanText(self.XPATH)(self.doc)

    def on_load(self):
        error = CleanText(self.XPATH)(self.doc)
        raise ActionNeeded(error)


class BoursePage(AbstractPage):
    PARENT = 'lcl'
    PARENT_URL = 'bourse'

    def get_logout_link(self):
        return Link('//a[@title="Retour à l\'accueil"]')(self.doc)


class BourseDisconnectPage(LoggedPage, HTMLPage):
    pass


class ProfilePage(LoggedPage, HTMLPage):
    @method
    class get_profile(ItemElement):
        klass = Person

        obj_birth_date = Date(CleanText('//td[text()="Date de naissance"]/following::td[1]'))
        obj_name = CleanText('//div[contains(@class,"tab-pane")]/table/thead/tr/th')
        obj_nationality = CleanText('//td[text()="Nationalité(s)"]/following::td[1]')
        obj_family_situation = CleanText('//td[text()="Situation Familiale"]/following::td[1]')
        obj_email = CleanText('//td[text()="Adresse e-mail"]/following::td[1]')
        obj_phone = CleanText('//td[text()="Téléphone portable"]/following::td[1]//td[1]')
        obj_country = CleanText('//td[text()="Pays"]/following::td[1]')
        obj_socioprofessional_category = CleanText('//td[text()="Situation professionnelle"]/following::td[1]')
        obj_address = Format(
            '%s %s %s',
            CleanText('//td[text()="Adresse"]/following::td[1]'),
            CleanText('//td[text()="Code postal"]/following::td[1]'),
            CleanText('//td[text()="Ville"]/following::td[1]')
        )
