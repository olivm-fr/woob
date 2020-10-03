# -*- coding: utf-8 -*-

# Copyright(C) 2016       Baptiste Delpey
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

import datetime
from decimal import Decimal
import re
from datetime import date
import hashlib
from functools import wraps

from weboob.browser.pages import (
    HTMLPage, LoggedPage, pagination, NextPage, FormNotFound, PartialHTMLPage,
    LoginPage, CsvPage, RawPage, JsonPage,
)
from weboob.browser.elements import ListElement, ItemElement, method, TableElement, SkipItem, DictElement
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Field, Format,
    Regexp, Date, AsyncLoad, Async, Eval, Env,
    Currency as CleanCurrency, Map, Coalesce,
    MapIn, Lower, Base,
)
from weboob.browser.filters.json import Dict
from weboob.browser.filters.html import Attr, Link, TableCell
from weboob.capabilities.bank import (
    Account as BaseAccount, Recipient, Transfer, AccountNotFound,
    AddRecipientBankError, TransferInvalidAmount, Loan, AccountOwnership,
    Emitter,
)
from weboob.capabilities.wealth import (
    Investment, MarketOrder, MarketOrderType, MarketOrderDirection, MarketOrderPayment,
)
from weboob.tools.capabilities.bank.investments import create_french_liquidity
from weboob.capabilities.base import NotAvailable, Currency, find_object, empty
from weboob.capabilities.profile import Person
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.capabilities.bank.iban import is_iban_valid
from weboob.tools.capabilities.bank.investments import IsinCode, IsinType
from weboob.tools.value import Value
from weboob.tools.date import parse_french_date
from weboob.tools.compat import urljoin, urlencode, urlparse, range
from weboob.exceptions import (
    BrowserQuestion, BrowserIncorrectPassword, BrowserHTTPNotFound, BrowserUnavailable,
    ActionNeeded,
)


class IncidentPage(HTMLPage):
    pass


class Account(BaseAccount):
    @property
    def _bourso_type(self):
        return re.search(r'/compte/([^/]+)/', self.url)[1]

    @property
    def _bourso_id(self):
        m = re.search(r'/compte/[^/]+/([a-f0-9]{32})/', self.url)
        if m:
            return m[1]


class IbanPage(LoggedPage, HTMLPage):
    def get_iban(self):
        if (
            self.doc.xpath('//div[has-class("alert")]/p[contains(text(), "Une erreur est survenue")]')
            or self.doc.xpath('//div[has-class("alert")]/p[contains(text(), "Le compte est introuvable")]')
        ):
            return NotAvailable
        return CleanText(
            '//div[strong[contains(text(),"IBAN")]]/div[contains(@class, "definition")]',
            replace=[(' ', '')]
        )(self.doc)


class AuthenticationPage(HTMLPage):
    def authenticate(self):
        self.logger.info('Using the PIN Code %s to login', self.browser.config['pin_code'].get())
        self.logger.info('Using the auth_token %s to login', self.browser.auth_token)

        form = self.get_form()
        form['otp_confirm[otpCode]'] = self.browser.config['pin_code'].get()
        form['flow_secureForm_instance'] = self.browser.auth_token
        form['otp_confirm[validate]'] = ''
        form['flow_secureForm_step'] = 2
        form.submit()

        self.browser.auth_token = None

    def get_confirmation_link(self):
        return Link('//a[contains(@href, "validation")]')(self.doc)

    def sms_first_step(self):
        """
        This function simulates the registration of a device on
        boursorama two factor authentification web page.
        """
        form = self.get_form()
        form.submit()

    def sms_second_step(self):
        # <div class="form-errors"><ul><li>Vous avez atteint le nombre maximal de demandes pour aujourd&#039;hui</li></ul></div>
        error = CleanText('//div[has-class("form-errors")]')(self.doc)
        if len(error) > 0:
            raise BrowserIncorrectPassword(error)

        form = self.get_form()
        # regular login way detection
        if 'flow_secureForm_instance' in form:
            self.browser.auth_token = form['flow_secureForm_instance']
            form['otp_prepare[receiveCode]'] = ''
            form.submit()

        raise BrowserQuestion(Value('pin_code', label='Enter the PIN Code'))


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^(Virement .* )?VIR( SEPA)? (?P<text>.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^CHQ\. (?P<text>.*)'), FrenchTransaction.TYPE_CHECK),
        (
            re.compile(r'^(ACHAT|PAIEMENT) CARTE (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) (?P<text>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(ACHAT |PAIEMENT )?CARTE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2}) (?P<text>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<text>.+)?(ACHAT|PAIEMENT) CARTE (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{4}) (?P<text2>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<text>.+)?(ACHAT|PAIEMENT) CARTE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) (?P<text2>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<text>.+)?((ACHAT|PAIEMENT)\s)?CARTE (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{4}) (?P<text2>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<text>.+)?((ACHAT|PAIEMENT)\s)?CARTE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) (?P<text2>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'(?P<text>.+) CARTE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2}) (?P<text2>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (re.compile(r'^(PRLV SEPA |PRLV |TIP )(?P<text>.*)'), FrenchTransaction.TYPE_ORDER),
        (
            re.compile(r'^RETRAIT DAB (?P<dd>\d{2})/?(?P<mm>\d{2})/?(?P<yy>\d{2}) (?P<text>.*)'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^([A-Z][\sa-z]* )?RETRAIT DAB (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{4}) (?P<text>.*)'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^([A-Z][\sa-z]* )?Retrait dab (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{4}) (?P<text>.*)'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^AVOIR (?P<dd>\d{2})/?(?P<mm>\d{2})/?(?P<yy>\d{2}) (?P<text>.*)'),
            FrenchTransaction.TYPE_PAYBACK,
        ),
        (
            re.compile(r'^(?P<text>[A-Z][\sa-z]* )?AVOIR (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{4}) (?P<text2>.*)'),
            FrenchTransaction.TYPE_PAYBACK,
        ),
        (re.compile('^REM CHQ (?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (
            re.compile(u'^([*]{3} solde des operations cb [*]{3} )?Relevé différé Carte (.*)'),
            FrenchTransaction.TYPE_CARD_SUMMARY,
        ),
        (re.compile(u'^[*]{3} solde des operations cb [*]{3}(.*)'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^Ech pret'), FrenchTransaction.TYPE_LOAN_PAYMENT),
        (
            re.compile(r'\*INTER(ETS DEBITEURS AU|\.BRUTS) (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2})'),
            FrenchTransaction.TYPE_BANK,
        ),
        (re.compile(r'^\*.*'), FrenchTransaction.TYPE_BANK),
    ]


class VirtKeyboardPage(HTMLPage):
    pass


class BoursoramaVirtKeyboard(object):
    # sha256 hexdigest of data in src of img
    symbols = {
        '0': '8560081e18568aba02ef3b1f7ac0e8b238cbbd21b70a5e919360ac456d45d506',
        '1': 'eadac6d6288cbd61524fd1a3078a19bf555735c7af13a2890e307263c4c7259b',
        '2': 'c54018639480788c02708b2d89651627dadf74048e029844f92006e19eadc094',
        '3': 'f3022aeced3b8f45f69c1ec001909636984c81b7e5fcdc2bc481668b1e84ae05',
        '4': '3e3d48446781f9f337858e56d01dd9a66c6be697ba34d8f63f48e694f755a480',
        '5': '4b16fb3592febdd9fb794dc52e4d49f5713e9af05486388f3ca259226dcd5cce',
        '6': '9b3afcc0ceb68c70cc697330d8a609900cf330b6aef1fb102f7a1c34cd8bc3d4',
        '7': '9e760193de1b6c5135ebe1bcad7ff65a2aacfc318973ce29ecb23ed2f86d6012',
        '8': '64d87d9a7023788e21591679c1783390011575a189ea82bb36776a096c7ca02c',
        '9': '1b358233ad4eb6b10bf0dadc3404261317a1b78b62f8501b70c646d654ae88f1',
    }

    def __init__(self, page, codesep='|'):
        self.codesep = codesep
        self.fingerprints = {}

        for button in page.doc.xpath('//ul[@class="password-input"]//button'):
            # src is like data:image/svg+xml;base64, [data]
            # so we split to only keep the data
            # hashed so that the symbols dict is smaller
            img_data_hash = hashlib.sha256(
                button.xpath('.//img')[0].attrib['src'].split()[1].encode('utf-8')
            ).hexdigest()
            self.fingerprints[img_data_hash] = button.attrib['data-matrix-key']

    def get_string_code(self, string):
        return self.codesep.join(
            self.fingerprints[self.symbols[digit]] for digit in string
        )


class PasswordPage(LoginPage, HTMLPage):
    TO_DIGIT = {
        'a': '2', 'b': '2', 'c': '2',
        'd': '3', 'e': '3', 'f': '3',
        'g': '4', 'h': '4', 'i': '4',
        'j': '5', 'k': '5', 'l': '5',
        'm': '6', 'n': '6', 'o': '6',
        'p': '7', 'q': '7', 'r': '7', 's': '7',
        't': '8', 'u': '8', 'v': '8',
        'w': '9', 'x': '9', 'y': '9', 'z': '9',
    }

    def enter_password(self, username, password):
        if not password.isdigit():
            old_password = password
            password = ''
            for c in old_password.lower():
                if c.isdigit():
                    password += c
                else:
                    password += self.TO_DIGIT[c]

        keyboard_page = self.browser.keyboard.open()
        vk = BoursoramaVirtKeyboard(keyboard_page)

        form = self.get_form()
        form['form[clientNumber]'] = username
        form['form[password]'] = vk.get_string_code(password)
        form['form[matrixRandomChallenge]'] = Regexp(CleanText('//script'), r'val\("(.*)"')(keyboard_page.doc)
        form.submit()

    def get_error(self):
        return CleanText('//h2[contains(text(), "Erreur")]/following-sibling::div[contains(@class, "msg")]')(self.doc)


class StatusPage(LoggedPage, PartialHTMLPage):
    def on_load(self):
        # sometimes checking accounts are missing
        msg = CleanText('//div[has-class("alert--danger")]', default=None)(self.doc)
        if msg:
            raise BrowserUnavailable(msg)


class AccountsPage(LoggedPage, HTMLPage):
    def is_here(self):
        # This id appears when there are no accounts (pro and pp)
        return not self.doc.xpath('//div[contains(@id, "alert-random")]')

    ACCOUNT_TYPES = {
        'comptes courants': Account.TYPE_CHECKING,
        'cav': Account.TYPE_CHECKING,
        'livret': Account.TYPE_SAVINGS,
        'livret-a': Account.TYPE_SAVINGS,
        'pel': Account.TYPE_SAVINGS,
        'cel': Account.TYPE_SAVINGS,
        'ldd': Account.TYPE_SAVINGS,
        'csl': Account.TYPE_SAVINGS,
        'comptes épargne': Account.TYPE_SAVINGS,
        'mon épargne': Account.TYPE_SAVINGS,
        'csljeune': Account.TYPE_SAVINGS,  # in url
        'ord': Account.TYPE_MARKET,
        'comptes bourse': Account.TYPE_MARKET,
        'mes placements financiers': Account.TYPE_MARKET,
        'av': Account.TYPE_LIFE_INSURANCE,
        'assurances vie': Account.TYPE_LIFE_INSURANCE,
        'assurance-vie': Account.TYPE_LIFE_INSURANCE,
        'mes crédits': Account.TYPE_LOAN,
        'crédit': Account.TYPE_LOAN,
        'prêt': Account.TYPE_LOAN,
        'pea': Account.TYPE_PEA,
        'carte': Account.TYPE_CARD,
    }

    ACCOUNTS_OWNERSHIP = {
        'Comptes de mes enfants': AccountOwnership.ATTORNEY,
        'joint': AccountOwnership.CO_OWNER,
        'commun': AccountOwnership.CO_OWNER,
    }

    @method
    class iter_accounts(ListElement):
        item_xpath = '//table[@class="table table--accounts"]/tr[has-class("table__line--account") and count(descendant::td) > 1 and @data-line-account-href]'

        class item(ItemElement):
            klass = Account

            load_details = Field('url') & AsyncLoad

            def condition(self):
                # Ignore externally aggregated accounts and insurances:
                return (
                    not self.is_external()
                    and not re.search(
                        'automobile|assurance/protection|assurance/comptes|assurance/famille',
                        Field('url')(self)
                    )
                )

            obj_label = CleanText('.//a[has-class("account--name")] | .//div[has-class("account--name")]')
            obj_currency = FrenchTransaction.Currency('.//a[has-class("account--balance")]')
            obj_valuation_diff = (
                Async('details')
                & CleanDecimal(
                    '''//li[h4[text()="Total des +/- values"]]/h3 |
                    //li[span[text()="Total des +/- values latentes"]]/span[has-class("overview__value")]''',
                    replace_dots=True, default=NotAvailable
                )
            )
            obj__holder = None

            obj__amount = CleanDecimal.French('.//a[has-class("account--balance")]')

            def obj_balance(self):
                if Field('type')(self) != Account.TYPE_CARD:
                    balance = Field('_amount')(self)
                    if Field('type')(self) in [Account.TYPE_PEA, Account.TYPE_LIFE_INSURANCE, Account.TYPE_MARKET]:
                        page = Async('details').loaded_page(self)
                        if isinstance(page, MarketPage):
                            updated_balance = page.get_balance(Field('type')(self))
                            if updated_balance is not None:
                                return updated_balance
                    return balance
                return Decimal('0')

            def obj_coming(self):
                # report deferred expenses in the coming attribute
                if Field('type')(self) == Account.TYPE_CARD:
                    return Field('_amount')(self)
                return Async(
                    'details',
                    CleanDecimal(
                        u'//li[h4[text()="Mouvements à venir"]]/h3', replace_dots=True, default=NotAvailable
                    )
                )(self)

            def obj_id(self):
                type = Field('type')(self)
                if type == Account.TYPE_CARD:
                    # When card is opposed it still appears on accounts page with a dead link and so, no id. Skip it.
                    if Attr('.//a[has-class("account--name")]', 'href')(self) == '#':
                        raise SkipItem()
                    return self.obj__idparts()[1]

                # sometimes it's <div> sometimes it's <h3>
                id = Async(
                    'details',
                    Regexp(
                        CleanText('//*[has-class("account-number")]', transliterate=True),
                        r'Reference du compte : (\d+)',
                        default=NotAvailable
                    )
                )(self)
                if not id:
                    raise SkipItem()
                return id

            obj_number = obj_id

            def obj_type(self):
                # card url is /compte/cav/xxx/carte/yyy so reverse to match "carte" before "cav"
                for word in Field('url')(self).lower().split('/')[::-1]:
                    v = self.page.ACCOUNT_TYPES.get(word)
                    if v:
                        return v

                for word in Field('label')(self).replace('_', ' ').lower().split():
                    v = self.page.ACCOUNT_TYPES.get(word)
                    if v:
                        return v

                category = CleanText('./preceding-sibling::tr[has-class("list--accounts--master")]//h4')(self)
                v = self.page.ACCOUNT_TYPES.get(category.lower())
                if v:
                    return v

                page = Async('details').loaded_page(self)
                if isinstance(page, LoanPage):
                    return Account.TYPE_LOAN

                return Account.TYPE_UNKNOWN

            def obj_ownership(self):
                ownership = Coalesce(
                    MapIn(
                        CleanText('../tr[contains(@class, "list--accounts--master")]//h4/text()'),
                        self.page.ACCOUNTS_OWNERSHIP,
                        default=NotAvailable
                    ),
                    MapIn(
                        Lower(Field('label')),
                        self.page.ACCOUNTS_OWNERSHIP,
                        default=NotAvailable
                    ),
                    default=NotAvailable
                )(self)

                return ownership

            def obj_url(self):
                link = Attr('.//a[has-class("account--name")] | .//a[2] | .//div/a', 'href', default=NotAvailable)(self)
                return urljoin(self.page.url, link)

            def is_external(self):
                return '/budget/' in Field('url')(self)

            def obj__idparts(self):
                return re.findall(r'[a-z\d]{32}', Field('url')(self))

            def obj__webid(self):
                parts = self.obj__idparts()
                if parts:
                    return parts[0]

            # We do not yield other banks accounts for the moment.
            def validate(self, obj):
                return (
                    not Async('details', CleanText(u'//h4[contains(text(), "Établissement bancaire")]'))(self)
                    and not Async('details', CleanText(u'//h4/div[contains(text(), "Établissement bancaire")]'))(self)
                )


class LoanPage(LoggedPage, HTMLPage):

    LOAN_TYPES = {
        "PRÊT PERSONNEL": Account.TYPE_CONSUMER_CREDIT,
        "CLIC": Account.TYPE_CONSUMER_CREDIT,
    }

    @method
    class get_loan(ItemElement):

        klass = Loan

        obj_id = CleanText('//h3[contains(@class, "account-number")]/strong')
        obj_label = CleanText(r'//h2[contains(@class, "page-title__account")]//*[@class="account-edit-label"]/span[1]')
        obj_currency = CleanCurrency('//p[contains(text(), "Solde impayé")]/span')
        obj_duration = CleanDecimal.French('//p[contains(text(), "échéances restantes")]/span', default=NotAvailable)
        obj_rate = CleanDecimal.French(
            '//p[contains(text(), "Taux nominal en vigueur du prêt")]/span',
            default=NotAvailable
        )
        obj_nb_payments_left = CleanDecimal.French(
            '//p[contains(text(), "échéances restantes")]/span',
            default=NotAvailable
        )
        obj_next_payment_amount = CleanDecimal.French(
            '//p[contains(text(), "Montant de la prochaine échéance")]/span',
            default=NotAvailable
        )
        obj_nb_payments_total = CleanDecimal.French('//p[contains(text(), "écheances totales") or contains(text(), "Nombre total")]/span')
        obj_subscription_date = Date(
            CleanText('//p[contains(text(), "Date de départ du prêt")]/span'),
            parse_func=parse_french_date
        )

        def obj_total_amount(self):
            total_amount = CleanText('//p[contains(text(), "Montant emprunt")]/span')(self)
            if total_amount:
                return CleanDecimal.French('//p[contains(text(), "Montant emprunt")]/span')(self)
            return CleanDecimal.French('//div[contains(text(), "Montant emprunt")]/following-sibling::div')(self)

        def obj_maturity_date(self):
            maturity_date = CleanText('//p[contains(text(), "échéance finale")]/span')(self)
            if maturity_date:
                # Sometimes there is no maturity date, so instead there is just a dash
                if maturity_date == '-':
                    return NotAvailable
                return Date(
                    CleanText('//p[contains(text(), "échéance finale")]/span'),
                    parse_func=parse_french_date
                )(self)

            return Date(
                Regexp(CleanText('//p[contains(text(), "date de votre dernière échéance")]'), r'(\d.*)'),
                parse_func=parse_french_date, default=NotAvailable
            )(self)

        def obj_balance(self):
            balance = CleanDecimal.French('//div[contains(text(), "Capital restant dû")]/following-sibling::div')(self)
            if balance > 0:
                balance *= -1
            return balance

        def obj_type(self):
            _type = CleanText('//h2[contains(@class, "page-title__account")]//div[@class="account-edit-label"]/span')
            return Map(_type, self.page.LOAN_TYPES, default=Account.TYPE_LOAN)(self)

        def obj_next_payment_date(self):
            tmp = CleanText('//p[contains(text(), "Date de la prochaine échéance")]/span')(self)
            if tmp in ('', '-'):
                return NotAvailable
            return Date(CleanText('//div[contains(text(), "Prochaine échéance")]/following-sibling::div'))(self)


class NoAccountPage(LoggedPage, HTMLPage):
    def is_here(self):
        err = CleanText('//div[contains(@id, "alert-random")]/text()', children=False)(self.doc)
        return "compte inconnu" in err.lower()


class CardCalendarPage(LoggedPage, RawPage):
    def is_here(self):
        return b'VCALENDAR' in self.doc

    def on_load(self):
        page_content = self.content.decode('utf-8')
        self.browser.deferred_card_calendar = []

        # handle ics calendar
        dates = page_content.split('BEGIN:VEVENT')[1:]
        assert len(dates) % 2 == 0, 'List length should be even-numbered'

        # get all dates
        dates = [re.search(r'(?<=VALUE\=DATE:)(\d{8})', el).group(1) for el in dates]
        dates.sort()

        for i in range(0, len(dates), 2):
            if len(dates[i:i + 2]) == 2:
                # list contains tuple like (vdate, date)
                self.browser.deferred_card_calendar.append(
                    (
                        Date().filter(dates[i]),
                        Date().filter(dates[i + 1]),
                    )
                )


class CalendarPage(LoggedPage, HTMLPage):
    def on_load(self):
        # redirect
        calendar_ics_url = urljoin(
            self.browser.BASEURL,
            CleanText('//a[contains(@href, "calendrier.ics")]/@href')(self.doc)
        )
        self.browser.location(calendar_ics_url)


def otp_pagination(func):
    @wraps(func)
    def inner(page, *args, **kwargs):
        while True:
            try:
                for r in func(page, *args, **kwargs):
                    yield r
            except NextPage as e:
                result = page.browser.otp_location(e.request)
                if result is None:
                    return

                page = result.page
            else:
                return

    return inner


class HistoryPage(LoggedPage, HTMLPage):
    """
    be carefull : `transaction_klass` is used in another page
    of an another module which is an abstract of this page
    """
    transaction_klass = Transaction

    @otp_pagination
    @method
    class iter_history(ListElement):
        item_xpath = '''
            //ul[has-class("list__movement")]/li[div and not(contains(@class, "summary"))
                and not(contains(@class, "graph"))
                and not(contains(@class, "separator"))
                and not(contains(@class, "list__movement__line--deffered"))]
        '''

        def next_page(self):
            next_page = self.el.xpath('//li[a[contains(text(), "Mouvements")]]')
            if next_page:
                next_page_token = Attr('.', 'data-operations-next-pagination')(next_page[0])
                params = {
                    'rumroute': 'accounts.bank.movements',
                    'continuationToken': next_page_token,
                }
                parsed = urlparse(self.page.url)
                return '%s://%s%s?%s' % (parsed.scheme, parsed.netloc, parsed.path, urlencode(params))

        class item(ItemElement):
            klass = Transaction

            obj_amount = CleanDecimal.French('.//div[has-class("list-operation-item__amount")]')
            obj_category = CleanText('.//span[contains(@class, "category")]')
            obj__account_name = CleanText('.//span[contains(@class, "account__name-xs")]', default=None)
            obj_raw = Transaction.Raw(
                Coalesce(
                    CleanText('.//span[has-class("list__movement--label-initial")]'),
                    CleanText('.//div[has-class("list-operation-item__label-name")]')
                )
            )

            def obj_id(self):
                return (
                    Attr('.', 'data-id', default=NotAvailable)(self)
                    or Attr('.', 'data-custom-id', default=NotAvailable)(self)
                )

            def obj_type(self):
                # In order to set TYPE_DEFERRED_CARD transactions correctly,
                # we must check if the transaction's account_name is in the list
                # of deferred cards, but summary transactions must escape this rule.
                if self.obj.type == Transaction.TYPE_CARD_SUMMARY:
                    return self.obj.type

                deferred_card_labels = [card.label for card in self.page.browser.cards_list]
                if Field('_account_name')(self).upper() in deferred_card_labels:
                    return Transaction.TYPE_DEFERRED_CARD

                is_card = Env('is_card', default=False)(self)
                if is_card:
                    if 'CARTE' in self.obj.raw:
                        return Transaction.TYPE_DEFERRED_CARD
                else:
                    if Env('coming', default=False)(self) and Field('raw')(self).startswith('CARTE '):
                        return Transaction.TYPE_CARD_SUMMARY

                # keep the value previously set by Transaction.Raw
                return self.obj.type

            def obj_rdate(self):
                if self.obj.rdate:
                    # Transaction.Raw may have already set it
                    return self.obj.rdate

                s = Regexp(Field('raw'), r' (\d{2}/\d{2}/\d{2}) | (?!NUM) (\d{6}) ', default=NotAvailable)(self)
                if not s:
                    return Field('date')(self)
                s = s.replace('/', '')
                # Sometimes the user enters an invalid date 16/17/19 for example
                return Date(dayfirst=True, default=NotAvailable).filter('%s-%s-%s' % (s[:2], s[2:4], s[4:]))

            def obj__is_coming(self):
                return (
                    Env('coming', default=False)(self)
                    or len(self.xpath('.//span[@title="Mouvement à débit différé"]'))
                    or self.obj_date() > date.today()
                )

            def obj_date(self):
                # Months with accents are retrieved like that: f\xe9vrier
                date = Date(
                    CleanText('./preceding-sibling::li[contains(@class, "date-line")][1]', transliterate=True),
                    parse_func=parse_french_date,
                )(self)

                if Env('is_card', default=False)(self):
                    if self.page.browser.deferred_card_calendar is None:
                        self.page.browser.location(Link('//a[contains(text(), "calendrier")]')(self))
                    closest = self.page.browser.get_debit_date(date)
                    if closest:
                        return closest

                return date

            def validate(self, obj):
                # TYPE_DEFERRED_CARD transactions are already present in the card history
                # so we only return TYPE_DEFERRED_CARD for the coming:
                if not Env('coming', default=False)(self):
                    is_card = Env('is_card', default=False)(self)
                    return (
                        is_card or (
                            not len(self.xpath('.//span[has-class("icon-carte-bancaire")]'))
                            and not len(self.xpath('.//a[contains(@href, "/carte")]'))
                            and obj.type != Transaction.TYPE_DEFERRED_CARD
                        )
                    )
                elif Env('coming', default=False)(self):
                    # Do not return coming from deferred cards if their
                    # summary does not have a fixed amount yet:
                    return obj.type != Transaction.TYPE_CARD_SUMMARY
                else:
                    return True

            def condition(self):
                # Users can split their transactions if they want. We don't want this kind
                # of custom transaction because:
                #  - The sum of this transactions can be different than the original transaction
                #     ex: The real transaction as an amount of 100€, the user is free to split it on 50€ and 60€
                #  - The original transaction is scraped anyway and we don't want duplicates
                return not self.xpath('./div[has-class("list__movement__line--block__split")]')

    def get_cards_number_link(self):
        return Link('//a[small[span[contains(text(), "carte bancaire")]]]', default=NotAvailable)(self.doc)

    def get_csv_link(self):
        return Link(
            '//a[@data-operations-export-button and not(has-class("hidden"))]',
            default=None
        )(self.doc)

    def get_calendar_link(self):
        return Link('//a[contains(text(), "calendrier")]')(self.doc)


class CardHistoryPage(LoggedPage, CsvPage):
    ENCODING = 'latin-1'
    FMTPARAMS = {'delimiter': str(';')}
    HEADER = 1

    @method
    class iter_history(DictElement):
        class item(ItemElement):
            klass = Transaction

            obj_raw = Transaction.Raw(Dict('label'))
            obj_bdate = Date(Dict('dateOp'))

            def obj_date(self):
                return self.page.browser.get_debit_date(Field('bdate')(self))

            obj__account_label = Dict('accountLabel')
            obj__is_coming = False

            def obj_amount(self):
                if Field('type')(self) == Transaction.TYPE_CARD_SUMMARY:
                    # '-' so the reimbursements appear positively in the card transactions:
                    return -CleanDecimal.French(Dict('amount'))(self)
                return CleanDecimal.French(Dict('amount'))(self)

            def obj_rdate(self):
                if self.obj.rdate:
                    # Transaction.Raw may have already set it
                    return self.obj.rdate

                s = Regexp(Field('raw'), r' (\d{2}/\d{2}/\d{2}) | (?!NUM) (\d{6}) ', default=NotAvailable)(self)
                if not s:
                    return Field('date')(self)
                s = s.replace('/', '')
                # Sometimes the user enters an invalid date 16/17/19 for example
                return Date(dayfirst=True, default=NotAvailable).filter('%s%s%s%s%s' % (s[:2], '-', s[2:4], '-', s[4:]))

            def obj_type(self):
                if 'CARTE' in self.obj.raw:
                    return Transaction.TYPE_DEFERRED_CARD
                return self.obj.type

            def obj_category(self):
                return Dict('category')(self)


class Myiter_investment(TableElement):
    # We do not scrape the investments contained in the "Engagements en liquidation" table
    # so we must check that the <h3> before the <div><table> does not contain this title.
    item_xpath = '//div[preceding-sibling::h3[1][text()!="Engagements en liquidation"]]//table[contains(@class, "operations")]/tbody/tr'
    head_xpath = '//div[preceding-sibling::h3[1][text()!="Engagements en liquidation"]]//table[contains(@class, "operations")]/thead/tr/th'

    col_value = u'Valeur'
    col_quantity = u'Quantité'
    col_unitprice = u'Px. Revient'
    col_unitvalue = u'Cours'
    col_valuation = u'Montant'
    col_diff = u'+/- latentes'


class Myitem(ItemElement):
    klass = Investment

    obj_quantity = CleanDecimal(TableCell('quantity'), default=NotAvailable)
    obj_unitprice = CleanDecimal(TableCell('unitprice'), replace_dots=True, default=NotAvailable)
    obj_unitvalue = CleanDecimal(TableCell('unitvalue'), replace_dots=True, default=NotAvailable)
    obj_valuation = CleanDecimal(TableCell('valuation'), replace_dots=True, default=NotAvailable)
    obj_diff = CleanDecimal(TableCell('diff'), replace_dots=True, default=NotAvailable)

    def obj_label(self):
        return CleanText().filter((TableCell('value')(self)[0]).xpath('.//a'))

    def obj_code(self):
        return CleanText().filter((TableCell('value')(self)[0]).xpath('./span')) or NotAvailable


def my_pagination(func):
    def inner(page, *args, **kwargs):
        while True:
            try:
                for r in func(page, *args, **kwargs):
                    yield r
            except NextPage as e:
                try:
                    result = page.browser.location(e.request)
                    page = result.page
                except BrowserHTTPNotFound as e:
                    page.logger.warning(e)
                    return
            else:
                return
    return inner


MARKET_ORDER_TYPES = {
    'LIM': MarketOrderType.LIMIT,
    'AM': MarketOrderType.MARKET,
    'ASD': MarketOrderType.TRIGGER,
}

MARKET_DIRECTIONS = {
    'Achat': MarketOrderDirection.BUY,
    'Vente': MarketOrderDirection.SALE,
}

MARKET_ORDER_PAYMENTS = {
    'Comptant': MarketOrderPayment.CASH,
    'Règlement différé': MarketOrderPayment.DEFERRED,
}


class MarketPage(LoggedPage, HTMLPage):
    def get_balance(self, account_type):
        if account_type == Account.TYPE_LIFE_INSURANCE:
            txt = "Solde au"
        else:
            txt = "Total Portefeuille"
        # HTML tags are usually h4-h3 but may also be span-span
        h_balance = CleanDecimal('//li[h4[contains(text(), "%s")]]/h3' % txt, replace_dots=True, default=None)(self.doc)
        span_balance = CleanDecimal(
            '//li/span[contains(text(), "%s")]/following-sibling::span' % txt,
            replace_dots=True, default=None
        )(self.doc)
        return h_balance or span_balance or None

    def get_market_order_link(self):
        return Link('//a[contains(@data-url, "orders")]', default=None)(self.doc)

    @my_pagination
    @method
    class iter_history(TableElement):
        item_xpath = '//table/tbody/tr'
        head_xpath = '//table/thead/tr/th'

        col_label = ['Nature', u'Opération']
        col_amount = 'Montant'
        col_date = ["Date d'effet", 'Date', u'Date opération']

        next_page = Link('//li[@class="pagination__next"]/a')

        class item(ItemElement):
            klass = Transaction

            def obj_date(self):
                d = Date(CleanText(TableCell('date')), dayfirst=True, default=None)(self)
                if d:
                    return d
                return Date(CleanText(TableCell('date')), parse_func=parse_french_date)(self)

            obj_raw = Transaction.Raw(CleanText(TableCell('label')))
            obj_amount = CleanDecimal(TableCell('amount'), replace_dots=True, default=NotAvailable)
            obj__is_coming = False

            def parse(self, el):
                if el.xpath('./td[2]/a'):
                    m = re.search(r'(\d+)', el.xpath('./td[2]/a')[0].get('data-modal-alert-behavior', ''))
                    if m:
                        url = '%s%s%s' % (self.page.url.split('mouvements')[0], 'mouvement/', m.group(1))
                        page = self.page.browser.open(url).page
                        self.env['account']._history_pages.append((Field('raw')(self), page))
                        raise SkipItem()

    @method
    class get_investment(Myiter_investment):
        class item (Myitem):
            def obj_unitvalue(self):
                el = TableCell('unitvalue')(self)[0].xpath('./span[not(@class)]')
                return CleanDecimal(replace_dots=True, default=NotAvailable).filter(el)

    def iter_investment(self):
        # Xpath can be h3/h4 or div/span; in both cases
        # the first node contains "Solde Espèces":
        valuation = CleanDecimal(
            '//li/*[contains(text(), "Solde Espèces")]/following-sibling::*', replace_dots=True, default=None
        )(self.doc)
        if not empty(valuation):
            yield create_french_liquidity(valuation)

        for inv in self.get_investment():
            yield inv

    def get_transactions_from_detail(self, account):
        for label, page in account._history_pages:
            amounts = page.doc.xpath('//span[contains(text(), "Montant")]/following-sibling::span')
            if len(amounts) == 3:
                amounts.pop(0)
            for table in page.doc.xpath('//table'):
                t = Transaction()

                t.date = Date(
                    CleanText(page.doc.xpath('//span[contains(text(), "Date d\'effet")]/following-sibling::span')),
                    dayfirst=True
                )(page)
                t.label = label
                t.amount = CleanDecimal(replace_dots=True).filter(amounts[0])
                amounts.pop(0)
                t._is_coming = False
                t.investments = []
                sum_amount = 0
                for tr in table.xpath('./tbody/tr'):
                    i = Investment()
                    i.label = CleanText().filter(tr.xpath('./td[1]'))
                    i.vdate = Date(CleanText(tr.xpath('./td[2]')), dayfirst=True)(tr)
                    i.unitvalue = CleanDecimal(replace_dots=True).filter(tr.xpath('./td[3]'))
                    i.quantity = CleanDecimal(replace_dots=True).filter(tr.xpath('./td[4]'))
                    i.valuation = CleanDecimal(replace_dots=True).filter(tr.xpath('./td[5]'))
                    sum_amount += i.valuation
                    t.investments.append(i)

                if t.label == 'prélèvement':
                    t.amount = sum_amount

                yield t

    @pagination
    @method
    class iter_market_orders(TableElement):
        item_xpath = '//table/tbody/tr[td]'
        head_xpath = '//table/thead/tr/th'

        col_date = 'Date'
        col_label = 'Libellé'
        col_direction = 'Sens'
        col_state = 'Etat'
        col_quantity = 'Qté'
        col_order_type = 'Type'
        col_unitvalue = 'Cours'
        col_validity_date = 'Validité'
        col_stock_market = 'Marché'

        next_page = Link('//li[@class="pagination__next"]//a', default=None)

        class item(ItemElement):
            klass = MarketOrder

            obj_id = Base(TableCell('date'), CleanText('.//a'))
            obj_label = CleanText(TableCell('label'), children=False)
            obj_direction = Map(CleanText(TableCell('direction')), MARKET_DIRECTIONS, MarketOrderDirection.UNKNOWN)
            obj_code = IsinCode(Base(TableCell('label'), CleanText('.//a')))
            obj_currency = CleanCurrency(TableCell('unitvalue'))

            # The column contains the payment_method and the stock market (e.g. 'Comptant Euronext')
            # We select the stock_market by using the <br> between the two.
            obj_stock_market = Base(
                TableCell('stock_market'),
                CleanText('./text()[2]'),
                default=NotAvailable
            )
            obj_payment_method = MapIn(
                CleanText(TableCell('stock_market')),
                MARKET_ORDER_PAYMENTS,
                MarketOrderPayment.UNKNOWN
            )

            # Unitprice may be absent if the order is still ongoing
            obj_unitprice = CleanDecimal.US(TableCell('state'), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'))
            obj_ordervalue = CleanDecimal.French(TableCell('order_type'), default=NotAvailable)
            obj_quantity = CleanDecimal.SI(TableCell('quantity'))

            obj_date = Date(Base(TableCell('date'), CleanText('.//span')), dayfirst=True)
            obj_validity_date = Date(CleanText(TableCell('validity_date')), dayfirst=True)

            # Text format looks like 'LIM 49,000', we only use the 'LIM' for typing
            obj_order_type = MapIn(CleanText(TableCell('order_type')), MARKET_ORDER_TYPES, MarketOrderType.UNKNOWN)
            # Text format looks like 'Exécuté 12.345 $' or 'En cours', we only fetch the first words
            obj_state = CleanText(Regexp(CleanText(TableCell('state')), r'^(\D+)'))


class SavingMarketPage(MarketPage):
    @pagination
    @method
    class iter_history(TableElement):
        item_xpath = '//table/tbody/tr'
        head_xpath = '//table/thead/tr/th'

        col_label = u'Opération'
        col_amount = u'Montant'
        col_date = u'Date opération'
        col_vdate = u'Date Val'

        next_page = Link('//li[@class="pagination__next"]/a')

        class item(ItemElement):
            klass = Transaction

            obj_label = CleanText(TableCell('label'))
            obj_amount = CleanDecimal(TableCell('amount'), replace_dots=True)
            obj__is_coming = False

            def obj_date(self):
                return parse_french_date(CleanText(TableCell('date'))(self))

            def obj_vdate(self):
                return parse_french_date(CleanText(TableCell('vdate'))(self))

    @method
    class iter_investment(TableElement):
        item_xpath = '//table/tbody/tr[count(descendant::td) > 4]'
        head_xpath = '//table/thead/tr[count(descendant::th) > 4]/th'

        col_label = u'Fonds'
        col_code = u'Code Isin'
        col_unitvalue = u'Valeur de la part'
        col_quantity = u'Nombre de parts'
        col_vdate = u'Date VL'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_code = IsinCode(CleanText(TableCell('code')))
            obj_code_type = IsinType(CleanText(TableCell('code')))
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'))
            obj_quantity = CleanDecimal.SI(TableCell('quantity'))
            obj_valuation = Eval(lambda x, y: x * y, Field('quantity'), Field('unitvalue'))
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True)


class AsvPage(MarketPage):
    @method
    class iter_investment(Myiter_investment):
        col_vdate = u'Date de Valeur'
        col_label = u'Valeur'

        class item(Myitem):
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True, default=NotAvailable)

            def obj_label(self):
                return CleanText('.//strong/a')(self) or CleanText('.//strong', children=False)(self)


class ErrorPage(HTMLPage):
    def on_load(self):
        error = (
            Attr('//input[@required][@id="profile_lei_type_identifier"]', 'data-message', default=None)(self.doc)
            or CleanText('//h2[@class="page-title"][contains(text(), "Actualisation")]', default=None)(self.doc)
        )
        if error:
            raise ActionNeeded(error)


class ExpertPage(LoggedPage, HTMLPage):
    pass


def MyInput(*args, **kwargs):
    args = (u'//input[contains(@name, "%s")]' % args[0], 'value',)
    kwargs.update(default=NotAvailable)
    return Attr(*args, **kwargs)


def MySelect(*args, **kwargs):
    args = (u'//select[contains(@name, "%s")]/option[@selected]' % args[0],)
    kwargs.update(default=NotAvailable)
    return CleanText(*args, **kwargs)


class ProfilePage(LoggedPage, HTMLPage):

    def get_children_firstnames(self):
        names = []

        for child in self.doc.xpath('//span[@class="transfer__account-name"]'):
            name = child.text.split('\n')
            assert len(name) > 1, "There is a child without firstname or the html code has changed !"
            names.append(child.text.split('\n')[0])

        return names

    @method
    class get_profile(ItemElement):
        klass = Person

        obj_name = Format('%s %s %s', MySelect('genderTitle'), MyInput('firstName'), MyInput('lastName'))
        obj_firstname = MyInput('firstName')
        obj_lastname = MyInput('lastName')
        obj_nationality = CleanText(u'//span[contains(text(), "Nationalité")]/span')
        obj_spouse_name = MyInput('spouseFirstName')
        obj_children = CleanDecimal(MyInput('dependentChildren'), default=NotAvailable)
        obj_family_situation = MySelect('maritalStatus')
        obj_matrimonial = MySelect('matrimonial')
        obj_housing_status = MySelect('housingSituation')
        obj_job = MyInput('occupation')
        obj_job_start_date = Date(MyInput('employeeSince'), default=NotAvailable)
        obj_company_name = MyInput('employer')
        obj_socioprofessional_category = MySelect('socioProfessionalCategory')


class CardsNumberPage(LoggedPage, HTMLPage):
    def populate_cards_number(self, cards):
        """
        Cards seems to be related to 2 hashs. The first one is already set in the account`id` (card.id)
        the second one is only findable in this page (which gives us the card number).
        We need to find the link between both hash to set the card number to the good account.
        """

        # We get all related card hashs in the page associate each one with the correct card account
        for _hash in self.doc.xpath('//div[contains(@class, "credit-card-carousel")]/@data-card-key'):
            # We get the card number associate to the cards_hash
            card_number = CleanText(
                '//div[@data-card-key="%s" and contains(@class, "credit-card-carousel")]' % _hash
                + '//*[local-name()="svg"]//*[local-name()="tspan"]',
                replace=[(' ', '')]
            )(self.doc)

            # There is only one place in the code where we can associate both hash to each other. The second hash
            # that we found with the first one match with a card account id.
            url = Link('//nav[@data-card-key="%s"]//a[contains(@href, "calendrier")]' % _hash, NotAvailable)(self.doc)

            # If there is no coming, that's not a deferred card
            if not empty(url):
                card_id = re.search(r'\/carte\/(.*)\/calendrier', url).group(1)
                card = find_object(cards, id=card_id, error=AccountNotFound)
                card.number = card_number


class HomePage(LoggedPage, HTMLPage):
    pass


class NoTransferPage(LoggedPage, HTMLPage):
    pass


class TransferMainPage(LoggedPage, HTMLPage):
    pass


class TransferAccounts(LoggedPage, HTMLPage):
    @method
    class iter_accounts(ListElement):
        item_xpath = '//a[has-class("next-step")][@data-value]'

        class item(ItemElement):
            klass = Account

            obj_id = CleanText('.//div[@class="transfer__account-number"]')
            obj__sender_id = Attr('.', 'data-value')

    def submit_account(self, id):
        for account in self.iter_accounts():
            if account.id == id:
                break
        else:
            raise AccountNotFound()

        form = self.get_form(name='DebitAccount')
        form['DebitAccount[debitAccountKey]'] = account._sender_id
        form.submit()

    @method
    class iter_emitters(ListElement):
        item_xpath = '//ul[@class="o-grid"]/li[@class="o-grid__item"]'

        class item(ItemElement):
            klass = Emitter

            obj_id = CleanText('.//div[contains(@class, "c-card-ghost__sub-label")]')
            obj_label = CleanText('.//div[contains(@class, "c-card-ghost__top-label")]')
            obj_currency = CleanCurrency('.//span[contains(@class, "c-card-ghost__side-panel")]')
            obj_balance = CleanDecimal.French('.//span[contains(@class, "c-card-ghost__side-panel")]')
            obj__bourso_id = Attr('.//div[has-class("c-card-ghost")]', 'data-value')


class TransferRecipients(LoggedPage, HTMLPage):
    @method
    class iter_recipients(ListElement):
        item_xpath = '//div[contains(@class, "deploy__wrapper")]//label'

        class item(ItemElement):
            klass = Recipient

            obj_id = CleanText('.//div[@class="c-card-ghost__sub-label"]')
            obj_bank_name = Regexp(
                CleanText('.//div[@class="transfer__account-name"]'), pattern=r'- ([^-]*)$',
                default=NotAvailable
            )

            def obj_label(self):
                label = Regexp(
                    CleanText('.//div[@class="c-card-ghost__top-label"]'),
                    pattern=r'^(.*?)(?: -[^-]*)?$'
                )(self)
                return label.rstrip('-').rstrip()

            def obj_category(self):
                text = CleanText('./ancestor::div[has-class("deploy--item")]//span')(self).lower()
                if 'mes comptes boursorama banque' in text:
                    return 'Interne'
                elif any(exp in text for exp in ('comptes externes', 'comptes de tiers', 'mes bénéficiaires')):
                    return 'Externe'

            def obj_iban(self):
                if Field('category')(self) == 'Externe':
                    return Field('id')(self)

            def obj_enabled_at(self):
                return datetime.datetime.now().replace(microsecond=0)

            obj__tempid = Attr('./input', 'value')

            def condition(self):
                iban = Field('iban')(self)
                if iban:
                    return is_iban_valid(iban)
                # some internal accounts don't show iban
                return True

    def submit_recipient(self, tempid):
        form = self.get_form(name='CreditAccount')
        form['CreditAccount[creditAccountKey]'] = tempid
        form.submit()


class NewTransferRecipients(LoggedPage, HTMLPage):
    @method
    class iter_recipients(ListElement):
        item_xpath = '//div[contains(@id, "panel-")]//div[contains(@class, "panel__body")]//label'

        class item(ItemElement):
            klass = Recipient

            obj_id = CleanText(
                './/span[contains(@class, "sub-label")]/span[not(contains(@class,"sub-label"))]',
                replace=[(' ', '')],
            )

            obj_label = Regexp(
                CleanText('.//span[contains(@class, "account-label")]'),
                r'([^-]+)',
                '\\1',
            )

            def obj_category(self):
                text = CleanText(
                    './ancestor::div[contains(@class, "panel__body")]'
                    + '/preceding-sibling::div[contains(@class, "panel__header")]'
                    + '//span[contains(@class, "panel__title")]'
                )(self).lower()
                if 'mes comptes boursorama banque' in text:
                    return 'Interne'
                elif any(exp in text for exp in ('comptes externes', 'comptes de tiers', 'mes bénéficiaires')):
                    return 'Externe'

            def obj_iban(self):
                if Field('category')(self) == 'Externe':
                    return Field('id')(self)
                return NotAvailable

            def obj_enabled_at(self):
                return datetime.datetime.now().replace(microsecond=0)

            obj__tempid = Attr('./input', 'value')


class NewTransferAccounts(LoggedPage, HTMLPage):
    def submit_account(self, account_id):
        form = self.get_form()
        debit_account = CleanText(
            '//input[./following-sibling::div/span/span[contains(text(), "%s")]]/@value' % account_id
        )(self.doc)
        if not debit_account:
            raise AccountNotFound()

        form['DebitAccount[debit]'] = debit_account
        form.submit()


class TransferCharac(LoggedPage, HTMLPage):
    def get_option(self, select, text):
        for opt in select.xpath('option'):
            if opt.text_content() == text:
                return opt.attrib['value']

    def submit_info(self, amount, label, exec_date):
        form = self.get_form(name='Characteristics')

        assert amount > 0
        amount = str(amount.quantize(Decimal('0.00'))).replace('.', ',')
        form['Characteristics[amount]'] = amount
        form['Characteristics[label]'] = label

        if not exec_date:
            exec_date = datetime.date.today()
        if datetime.date.today() == exec_date:
            assert self.get_option(form.el.xpath('//select[@id="Characteristics_schedulingType"]')[0], 'Ponctuel') == '1'
            form['Characteristics[schedulingType]'] = '1'
        else:
            assert self.get_option(form.el.xpath('//select[@id="Characteristics_schedulingType"]')[0], 'Différé') == '2'
            form['Characteristics[schedulingType]'] = '2'
            form['Characteristics[scheduledDate]'] = exec_date.strftime('%d/%m/%Y')

        form['Characteristics[notice]'] = 'none'
        form.submit()


class TransferConfirm(LoggedPage, HTMLPage):
    def on_load(self):
        errors = CleanText('//li[contains(text(), "Le montant du virement est inférieur au minimum")]')(self.doc)
        if errors:
            raise TransferInvalidAmount(message=errors)

    def need_refresh(self):
        return not self.doc.xpath('//form[@name="Confirm"]//button[contains(text(), "Valider")]')

    @method
    class get_transfer(ItemElement):
        klass = Transfer

        obj_label = CleanText('//span[@id="transfer-label"]/span[@class="transfer__account-value"]')
        obj_amount = CleanDecimal.French('//span[@id="transfer-amount"]/span[@class="transfer__account-value"]')
        obj_currency = CleanCurrency('//span[@id="transfer-amount"]/span[@class="transfer__account-value"]')

        obj_account_label = CleanText('//span[@id="transfer-origin-account"]')
        obj_recipient_label = CleanText('//span[@id="transfer-destination-account"]')

        def obj_exec_date(self):
            type_ = CleanText('//span[@id="transfer-type"]/span[@class="transfer__account-value"]')(self)
            if type_ == 'Ponctuel':
                return datetime.date.today()
            elif type_ == 'Différé':
                return Date(
                    CleanText('//span[@id="transfer-date"]/span[@class="transfer__account-value"]'),
                    dayfirst=True
                )(self)

    def submit(self):
        form = self.get_form(name='Confirm')
        form.submit()


class TransferSent(LoggedPage, HTMLPage):
    def get_transfer_error(self):
        return CleanText('//form[@name="Confirm"]/div[@class="form-errors"]//li')(self.doc)


class AddRecipientPage(LoggedPage, HTMLPage):
    def on_load(self):
        super(AddRecipientPage, self).on_load()

        err = CleanText('//div[@class="form-errors"]', default=None)(self.doc)
        if err:
            raise AddRecipientBankError(message=err)

    def _is_form(self, **kwargs):
        try:
            self.get_form(**kwargs)
        except FormNotFound:
            return False
        return True

    def is_charac(self):
        return self._is_form(name='externalAccountsPrepareType')

    def submit_recipient(self, recipient):
        form = self.get_form(name='externalAccountsPrepareType')
        form['externalAccountsPrepareType[type]'] = 'tiers'
        form['externalAccountsPrepareType[label]'] = recipient.label
        # names are mandatory and are uneditable...
        form['externalAccountsPrepareType[beneficiaryLastname]'] = recipient.label
        form['externalAccountsPrepareType[beneficiaryFirstname]'] = recipient.label
        form['externalAccountsPrepareType[bank]'] = recipient.bank_name or 'Autre'
        form['externalAccountsPrepareType[iban]'] = recipient.iban
        form['submit'] = ''
        form.submit()

    def is_send_sms(self):
        return (
            self._is_form(name='strong_authentication_prepare')
            and Attr('//input[@id="strong_authentication_prepare_type"]', 'value')(self.doc) == 'brs-otp-sms'
        )

    def is_send_email(self):
        return (
            self._is_form(name='strong_authentication_prepare')
            and Attr('//input[@id="strong_authentication_prepare_type"]', 'value')(self.doc) == 'brs-otp-email'
        )

    def send_otp(self):
        form = self.get_form(name='strong_authentication_prepare')
        form.submit()

    def is_confirm_otp(self):
        return self._is_form(name='strong_authentication_confirm')

    def get_confirm_otp_form(self):
        form = self.get_form(name='strong_authentication_confirm')
        recipient_form = {k: v for k, v in form.items()}
        recipient_form['url'] = form.url
        return recipient_form

    def is_confirm_send_sms(self):
        return self._is_form(name='externalAccountsConfirmType')

    def confirm_send_sms(self):
        form = self.get_form(name='externalAccountsConfirmType')
        form.submit()

    def is_created(self):
        return CleanText('//p[contains(text(), "Le bénéficiaire a bien été ajouté.")]')(self.doc) != ""


class PEPPage(LoggedPage, HTMLPage):
    pass


class CurrencyListPage(HTMLPage):
    @method
    class iter_currencies(ListElement):
        item_xpath = '//select[@class="c-select currency-change"]/option'

        class item(ItemElement):
            klass = Currency

            obj_id = Attr('./.', 'value')

    def get_currency_list(self):
        CurIDList = []
        for currency in self.iter_currencies():
            currency.id = currency.id[0:3]
            if currency.id not in CurIDList:
                CurIDList.append(currency.id)
                yield currency


class CurrencyConvertPage(JsonPage):
    def get_rate(self):
        if 'error' not in self.doc:
            return Decimal(str(self.doc['rate']))


class AccountsErrorPage(LoggedPage, HTMLPage):
    def is_here(self):
        # some braindead error seems to affect many accounts until we retry
        return '[E10008]' in CleanText('//div')(self.doc)

    def on_load(self):
        raise BrowserUnavailable()
