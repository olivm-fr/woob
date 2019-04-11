# -*- coding: utf-8 -*-

# Copyright(C) 2013-2015  Romain Bignon
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

from dateutil.relativedelta import relativedelta
from datetime import date as ddate, datetime
from decimal import Decimal
import re

from weboob.browser.pages import HTMLPage, FormNotFound, pagination
from weboob.capabilities import NotAvailable
from weboob.capabilities.base import Currency
from weboob.capabilities.bank import (
    Account, Investment, Recipient, Transfer, TransferError, TransferBankError,
    AddRecipientBankError, Loan
)
from weboob.capabilities.contact import Advisor
from weboob.capabilities.profile import Profile
from weboob.exceptions import BrowserIncorrectPassword, BrowserUnavailable, ActionNeeded
from weboob.tools.capabilities.bank.transactions import FrenchTransaction as Transaction
from weboob.tools.date import parse_french_date, LinearDateGuesser
from weboob.tools.compat import urlparse, urljoin, unicode
from weboob.browser.elements import ListElement, TableElement, ItemElement, method
from weboob.browser.filters.standard import Date, CleanText, CleanDecimal, Currency as CleanCurrency, \
                                            Regexp, Format, Field
from weboob.browser.filters.html import Link, TableCell, ColumnNotFound, Attr


class TableCellSpan(TableCell):
    def __call__(self, item):
        for name in self.names:
            # TableElement starts at 0
            idx = item.parent.get_colnum(name)
            if idx is not None:
                colnum = 0
                for el in item.xpath('./td'):
                    if colnum == idx:
                        return [el]
                    try:
                        colnum += int(el.attrib.get('colspan', 1))
                    except (ValueError, AttributeError):
                        colnum += 1

        return self.default_or_raise(ColumnNotFound('Unable to find column %s' % ' or '.join(self.names)))


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True)
    return CleanDecimal(*args, **kwargs)


def MyDate(*args, **kwargs):
    kwargs.update(dayfirst=True)
    return Date(*args, **kwargs)


class MyLoggedPage(object):
    pass


class BasePage(HTMLPage):
    def on_load(self):
        self.get_current()

    def get_current(self):
        try:
            current_elem = self.doc.xpath('//div[@id="libPerimetre_2"]/span[@class="textePerimetre_2"]')[0]
        except IndexError:
            self.logger.debug('Can\'t update current perimeter on this page (%s).', type(self).__name__)
            return False
        self.browser.current_perimeter = CleanText().filter(current_elem).lower()
        return True

    def get_error(self):
        error = CleanText('//h1[@class="h1-erreur"]')(self.doc)
        if error:
            self.logger.error('Error detected: %s', error)
            return error

    @property
    def logged(self):
        if not isinstance(self, MyLoggedPage):
            return False

        return self.get_error() is None


class CollectePageMixin(object):
    """
    Multiple pages have the same url pattern: "/stb/collecteNI?fwkaid=...&fwkpid=...".
    Use some page text to determine which page it is.
    """

    IS_HERE_TEXT = None

    def is_here(self):
        for el in self.doc.xpath('//div[@class="boutons-act"]//h1'):
            labels = self.IS_HERE_TEXT
            if not isinstance(labels, (list, tuple)):
                labels = [labels]

            for label in labels:
                if label in CleanText('.')(el):
                    return True
        return False


class HomePage(BasePage):
    ENCODING = 'iso8859-15'

    def get_post_url(self):
        for script in self.doc.xpath('//script'):
            text = script.text
            if text is None:
                continue

            m = re.search(r'var chemin = "([^"]+)"', text, re.MULTILINE)
            if m:
                return m.group(1)

        return None

    def go_to_auth(self):
        form = self.get_form(name='bamaccess')
        form.submit()

    def get_publickey(self):
        return Regexp(CleanText('.'), r'public_key.+?(\w+)')(self.doc)


class NewWebsitePage(BasePage):
    pass


class LoginPage(BasePage):
    def on_load(self):
        if self.doc.xpath('//font[@class="taille2"]'):
            raise BrowserIncorrectPassword()

    def login(self, username, password):
        password = password[:6]
        imgmap = {}
        for td in self.doc.xpath('//table[@id="pave-saisie-code"]/tr/td'):
            a = td.find('a')
            num = a.text.strip()
            if num.isdigit():
                imgmap[num] = int(a.attrib['tabindex']) - 1

        try:
            form = self.get_form(name='formulaire')
        except FormNotFound:
            raise BrowserIncorrectPassword()
        if self.browser.new_login:
            form['CCPTE'] = username

        form['CCCRYC'] = ','.join(['%02d' % imgmap[c] for c in password])
        form['CCCRYC2'] = '0' * len(password)
        form.submit()

    def get_result_url(self):
        return self.response.text.strip()

    def get_accounts_url(self):
        for script in self.doc.xpath('//script'):
            text = script.text
            if text is None:
                continue
            m = re.search(r'idSessionSag = "([^"]+)"', script.text)
            if m:
                idSessionSag = m.group(1)
        return '%s%s%s%s' % (self.url, '?sessionSAG=', idSessionSag, '&stbpg=pagePU&actCrt=Synthcomptes&stbzn=btn&act=Synthcomptes')


class UselessPage(MyLoggedPage, BasePage):
    pass


class LoginErrorPage(BasePage):
    def on_load(self):
        if CleanText(u'//p[contains(text(), "momentanément indisponible")]', default=None)(self.doc):
            raise BrowserUnavailable()


class FirstVisitPage(BasePage):
    def on_load(self):
        raise ActionNeeded(u'Veuillez vous connecter au site du Crédit Agricole pour valider vos données personnelles, et réessayer ensuite.')


class AccountsPage(MyLoggedPage, BasePage):
    TYPES = {u'CCHQ':       Account.TYPE_CHECKING, # par
             u'CCOU':       Account.TYPE_CHECKING, # pro
             u'AUTO ENTRP': Account.TYPE_CHECKING, # pro
             u'DEVISE USD': Account.TYPE_CHECKING,
             u'EKO' :       Account.TYPE_CHECKING,
             u'DAV NANTI':  Account.TYPE_SAVINGS,
             u'LIV A':      Account.TYPE_SAVINGS,
             u'LIV A ASS':  Account.TYPE_SAVINGS,
             u'LDD':        Account.TYPE_SAVINGS,
             u'PEL':        Account.TYPE_SAVINGS,
             u'CEL':        Account.TYPE_SAVINGS,
             u'CODEBIS':    Account.TYPE_SAVINGS,
             u'LJMO':       Account.TYPE_SAVINGS,
             u'CSL':        Account.TYPE_SAVINGS,
             u'LEP':        Account.TYPE_SAVINGS,
             u'LEF':        Account.TYPE_SAVINGS,
             u'TIWI':       Account.TYPE_SAVINGS,
             u'CSL LSO':    Account.TYPE_SAVINGS,
             u'CSL CSP':    Account.TYPE_SAVINGS,
             u'ESPE INTEG': Account.TYPE_SAVINGS,
             u'DAV TIGERE': Account.TYPE_SAVINGS,
             u'CPTEXCPRO':  Account.TYPE_SAVINGS,
             u'CPTEXCENT':  Account.TYPE_SAVINGS,
             u'PRET PERSO': Account.TYPE_LOAN,
             u'P. ENTREPR': Account.TYPE_LOAN,
             u'P. HABITAT': Account.TYPE_LOAN,
             u'PRET 0%':    Account.TYPE_LOAN,
             u'INV PRO':    Account.TYPE_LOAN,
             u'TRES. PRO':  Account.TYPE_LOAN,
             u'PEA':        Account.TYPE_PEA,
             u'PEAP':       Account.TYPE_PEA,
             u'DAV PEA':    Account.TYPE_PEA,
             u'CPS':        Account.TYPE_MARKET,
             u'TITR':       Account.TYPE_MARKET,
             u'TITR CTD':   Account.TYPE_MARKET,
             u'PVERT VITA':  Account.TYPE_PERP,
             u'réserves de crédit':     Account.TYPE_CHECKING,
             u'prêts personnels':       Account.TYPE_LOAN,
             u'crédits immobiliers':    Account.TYPE_LOAN,
             u'épargne disponible':     Account.TYPE_SAVINGS,
             u'épargne à terme':        Account.TYPE_DEPOSIT,
             u'épargne boursière':      Account.TYPE_MARKET,
             u'assurance vie et capitalisation': Account.TYPE_LIFE_INSURANCE,
            }

    @pagination
    @method
    class iter_accounts(TableElement):
        head_xpath = '//table[@class="ca-table"]//tr/th'
        item_xpath = '//table[@class="ca-table"]//tr[contains(@class, "colcelligne") or contains(@class, "autre-devise")]'

        col_id = 'N° de compte'
        col_label = 'Type de compte'
        col_balance_op = 'En opération'
        col_balance_value = 'En valeur'
        col_currency = 'Devise'

        next_page =  Link('//div[@class="boutons-navig"]//div[@class="btnsuiteliste"]/a[@class="btnsuiteliste"]', default=None)

        class item(ItemElement):
            klass = Account

            obj_label = CleanText(TableCellSpan('label'))
            obj_id = CleanText(TableCellSpan('id'))
            obj_currency = CleanCurrency(TableCellSpan('currency'))

            def obj_balance(self):
                td = TableCellSpan('balance_op', default=NotAvailable)(self)
                if td:
                    return MyDecimal(td, default=NotAvailable)(self)
                return MyDecimal(TableCellSpan('balance_value'), default=NotAvailable)(self)

            def obj_type(self):
                return self.page.TYPES.get(Field('label')(self), Account.TYPE_UNKNOWN)

            def obj__perimeter(self):
                return self.page.browser.current_perimeter

            def obj__form(self):
                td = TableCell('id')(self)
                a = Link('.//a', default=None)(td[0])
                form = None
                if a and a.startswith('javascript:'):
                    form_name = re.search(r'frm\d+', a).group(0)
                    form = self.page.history_form(form_name)
                return form

            def obj_url(self):
                url = None
                td = TableCell('id')(self)
                a = Link('.//a', default=None)(td[0])
                if a and not a.startswith('javascript:'):
                    url = urljoin(self.url, a.replace(' ', '%20'))
                    url = re.sub('sessionSAG=[^&]+', 'sessionSAG={0}', url)
                return url

    def history_form(self, name):
        form = self.get_form(name=name)
        form['fwkaction'] = 'Releves'
        form['fwkcodeaction'] = 'Executer'
        return form

    def get_code_caisse(self):
        scripts = self.doc.xpath('//script[contains(., " codeCaisse")]')
        return re.search('var +codeCaisse *= *"([0-9]+)"', scripts[0].text).group(1)

    def _iter_idelcos_ids(self):
        for line in self.doc.xpath('//table[@class="ca-table"]//tr[@class="ligne-connexe"]'):
            # ignore line if preceding line is also a link to deferred card
            li = line.xpath('./preceding-sibling::tr')
            if li[-1].attrib.get('class') == 'ligne-connexe':
                continue
            try:
                link = line.xpath('.//a/@href')[0]
            except IndexError:
                continue

            parent_id = re.findall(r'> (?P<id>\d+)', CleanText(li)(self))[-1]
            yield link, parent_id

    def iter_idelcos(self):
        # Use a set because it is possible to see several times the same link.
        idelcos = set()
        for link, parent_id in self._iter_idelcos_ids():
            if link.startswith('javascript:'):
                m = re.search(r"javascript:fwkPUAvancerForm\('Cartes','(\w+)'\)", link)
                if m:
                    idelcos.add((m.group(1), parent_id))
            else:
                m = re.search('IDELCO=(\d+)&', link)
                if m:
                    idelcos.add((m.group(1), parent_id))
        return idelcos

    def get_idelco(self, account_idelco):
        for link, parent_id in self._iter_idelcos_ids():
            if link.startswith('javascript:'):
                # no need to fetch a "full" link
                return self.get_form(name=account_idelco)
            elif 'IDELCO=%s&' % account_idelco in link:
                return link

    def submit_card(self, name):
        form = name
        form['fwkaction'] = 'Cartes'
        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def check_perimeters(self):
        return len(self.doc.xpath('//a[@title="Espace Autres Comptes"]'))


class PerimeterPage(MyLoggedPage, BasePage):
    def check_multiple_perimeters(self):
        self.browser.perimeters = list()
        self.get_current()
        if self.browser.current_perimeter is None:
            return
        self.browser.perimeters.append(self.browser.current_perimeter)
        multiple = self.doc.xpath(u'//p[span/a[contains(text(), "Accès")]]')
        if not multiple:
            if not len(self.doc.xpath(u'//div[contains(text(), "Périmètre en cours de chargement. Merci de patienter quelques secondes.")]')):
                self.logger.debug('Possible error on this page.')
            # We change perimeter in this case to add the second one.
            self.browser.location(self.browser.chg_perimeter_url.format(self.browser.sag))
            if self.browser.page.get_error() is not None:
                self.browser.broken_perimeters.append('the other perimeter is broken')
                self.browser.do_login()
        else:
            for table in self.doc.xpath('//table[@class]'):
                space = CleanText().filter(table.find('caption').text.lower())
                for perim in table.xpath('.//label'):
                    perim = CleanText().filter(perim.text.lower())
                    self.browser.perimeters.append(u'%s : %s' % (space, perim))

    def get_perimeter_link(self, perimeter):
        caption = perimeter.split(' : ')[0].title()
        perim = perimeter.split(' : ')[1]
        for table in self.doc.xpath('//table[@class and caption[contains(text(), $caption)]]', caption=caption):
            for p in table.xpath(u'.//p[span/a[contains(text(), "Accès")]]'):
                if perim in CleanText().filter(p.find('label').text.lower()):
                    link = p.xpath('./span/a')[0].attrib['href']
                    return link


class ChgPerimeterPage(PerimeterPage):
    def on_load(self):
        if self.get_error() is not None:
            self.logger.debug('Error on ChgPerimeterPage')
            return
        self.get_current()

        # sometimes the perimeter use " & " and sometimes " et "
        # and can also be "&" instead of "et" (example: "metms" et "m&ms")
        if not (self.browser.current_perimeter in self.browser.perimeters or
                self.browser.current_perimeter.replace('et', '&') in self.browser.perimeters):
            assert len(self.browser.perimeters) == 1
            self.browser.perimeters.append(self.browser.current_perimeter)


class CardElement(ItemElement):
    obj_coming = MyDecimal('.//tr[@class="ligne-paire"]/td[@class="cel-num"]', replace_dots=True, default=Decimal('0'))

    obj__form = None

    obj_balance = Decimal('0')

    def obj_currency(self):
        cur = self.page.doc.xpath('//table/caption//span/text()[starts-with(.,"Montants en ")]')[0].replace("Montants en ", "")
        return Account.get_currency(cur)

    def obj__idelco(self):
        m = re.search('IDELCO=(\d+)&', self.page.url)
        if m:
            idelco = m.group(1)
        else:
            idelco = None
        return idelco

    def obj__perimeter(self):
        return self.page.browser.current_perimeter


class CardsPage(MyLoggedPage, BasePage):
    # cragr sends us this shit: <td  class="cel-texte"  >
    # Msft *<e01002ymrk,E010
    # </td>
    def build_doc(self, content):
        content = re.sub(br'\*<e[a-z\d]{9},E\d{3}', b'*', content)
        return super(CardsPage, self).build_doc(content)

    @method
    class iter_card(ListElement):
        item_xpath = '//table[@class="ca-table"][1]'

        class item(CardElement):
            klass = Account
            obj_type = Account.TYPE_CARD

            # example: 123456xxxxxx9878
            obj_number = CleanText('.//tr[@class="ligne-impaire"]/td[@class="cel-texte"][1]', replace=[(' ', ''), ('n°', '')])
            obj_id = Format('%s%s', Field('number'), CleanText('./caption/span[@class="tdb-cartes-prop"]/b', replace=[(' ', '')]))

            # this field is needed to check if we are on the right detail page.
            # example: 1234 56xx xxxx 9878
            def obj__spaced_number(self):
                return ' '.join(CleanText('.//tr[@class="ligne-impaire"]/td[@class="cel-texte"][1]')(self).split()[1:])

            obj_label = Format('%s - %s', CleanText('.//tr[@class="ligne-impaire ligne-bleu"]/th[@id="compte-1"]'), Regexp(CleanText('./caption/span[@class="tdb-cartes-prop"]/b'), '^(.*)\s*-\s*$'))

            def obj_url(self):
                return self.page.url

    @method
    class iter_cards(ListElement):
        item_xpath = '//table[caption[@class="caption tdb-cartes-caption" or @class="ca-table caption"]]'

        class item(CardElement):

            def condition(self):
                return self.el.xpath('.//tr[contains(@class, "ligne")]/td[has-class("cel-num")]')

            klass = Account
            obj_type = Account.TYPE_CARD

            # example: 123456xxxxxx9878
            obj_number = CleanText('./caption/span[@class="tdb-cartes-num"]', replace=[(' ', ''), ('n°', '')])

            obj_id = Format('%s%s', Field('number'), CleanText('./caption/span[@class="tdb-cartes-prop"]', replace=[(' ', '')]))

            # this field is needed to check if we are on the right detail page
            # example: 1234 56xx xxxx 9878
            def obj__spaced_number(self):
                return ' '.join(CleanText('./caption/span[@class="tdb-cartes-num"]')(self).split())

            obj_label = Format('%s - %s', CleanText('./caption/span[contains(@class, "tdb-cartes-carte")]'), Regexp(CleanText('./caption/span[@class="tdb-cartes-prop"]'), '^(.*)\s*$'))

            obj_currency = CleanCurrency(Regexp(CleanText('//span[contains(text(), "Montants")]'), r'Montants en (.*)'))

            def obj_url(self):
                # "paire" is for the coming month
                link = Link('.//tr[@class="ligne-paire"]//a[text()="Carte"]', default=None)(self)
                if link is None:
                    # but sometimes only the history month is present
                    link = Link('.//tr[@class="ligne-impaire"]//a[text()="Carte"]')(self)

                link = urljoin(self.page.url, re.sub(r'\s+', '', link))
                link = re.sub(r'sessionSAG=[^&]+', r'sessionSAG={0}', link)
                return link

            def obj__parent_id(self):
                return Regexp(CleanText('//table[@class="ca-table"][1]//tr[contains(@class, "ligne-bleu")]/th'), r'n°(\d+)')(self)

    def several_cards(self):
        return bool(self.doc.xpath('//table[caption[@class="caption tdb-cartes-caption" or @class="ca-table caption"]]'))

    def order_transactions(self):
        pass

    def get_next_url(self):
        links = self.doc.xpath('//font[@class="btnsuiteliste"]')
        if len(links) < 1:
            return None

        a = links[-1].find('a')
        if a.attrib.get('class', '') == 'liennavigationcorpspage':
            return a.attrib['href']

        return None

    def get_history(self, date_guesser, state=None, fetch_summary=False):
        seen = set()
        lines = self.doc.xpath('(//table[@class="ca-table"])[2]/tr')
        debit_date = None
        for i, line in enumerate(lines):
            is_balance = line.xpath('./td/@class="cel-texte cel-neg"')

            # It is possible to have three or four columns.
            cols = [CleanText().filter(td) for td in line.xpath('./td')]
            date = cols[0]
            label = cols[1]
            amount = cols[-1]

            t = Transaction()
            t.set_amount(amount)
            t.label = t.raw = label

            if is_balance:
                m = re.search('(\d+ [^ ]+ \d+)', label)
                if not m:
                    raise Exception('Unable to read card balance in history: %r' % label)
                if state is None:
                    debit_date = parse_french_date(m.group(1))
                else:
                    debit_date = state

                # Skip the first line because it is balance
                if i == 0 and not fetch_summary:
                    continue

                t.date = t.rdate = debit_date

                # Consider the second one as a positive amount to reset balance to 0.
                t.amount = -t.amount
                t.type = t.TYPE_CARD_SUMMARY
                state = t.date
            else:
                day, month = map(int, date.split('/', 1))
                t.rdate = date_guesser.guess_date(day, month)
                t.date = debit_date
                t.type = t.TYPE_DEFERRED_CARD

            try:
                t.id = t.unique_id(seen)
            except UnicodeEncodeError:
                self.logger.debug(t)
                self.logger.debug(t.label)
                raise

            yield state, t

    def is_on_right_detail(self, account):
        return len(self.doc.xpath(u'//h1[contains(text(), "Cartes - détail")]')) and\
               len(self.doc.xpath(u'//td[contains(text(), $number)] | //td[contains(text(), $id)] ', number=account.number, id=account._spaced_number))


class LoansPage(AccountsPage):
    @method
    class iter_loans(ListElement):
        '''
        The cragr website shows informations on a single table, we can't use TableElement the way we do in a normal case.
        The colums we need are in some special tr: the ones with the "tr-thead" class, when the informations we need are in the trs between each tr-thead.
        We are using TableElement as if it was a table each time between tr-thead.
        '''
        item_xpath = '//table[@class="ca-table"]//tr[@class="tr-thead"]'

        class iter_item(TableElement):
            head_xpath = './th'

            def find_elements(self):
                item_xpath = './following-sibling::tr'
                for el in self.el.xpath(item_xpath):
                    if not el.attrib.get('class', '').startswith('colcelligne'):
                        return
                    yield el

            col_label = re.compile('.*n°')
            col_next_payment_amount = 'Mensualité'
            col_total_amount = 'Montant initial'
            col_available_amount = 'Montant disponible'
            col_balance = 'Capital restant dû'
            col_currency = 'Devise'

            class item(ItemElement):
                klass = Loan

                def parse(self, el):
                    td = TableCell('label')(self)
                    link = Link('.//a', default=None)(td[0])
                    # if js is in link it is a conso loans
                    # TODO support the loans_conso
                    self.env['details'] = None
                    if link and not 'javascript' in link:
                        details = self.page.browser.open(link)
                        if not details.page.get_error():
                            self.env['details'] = details.page


                obj_total_amount = MyDecimal(TableCellSpan('total_amount', default=NotAvailable), default=NotAvailable)

                obj_next_payment_amount = MyDecimal(TableCellSpan('next_payment_amount', default=NotAvailable), default=NotAvailable)

                def obj_balance(self):
                    td = TableCellSpan('balance', default=NotAvailable)(self)
                    if td:
                        return -abs(MyDecimal(td)(self))
                    return Decimal('0.00')

                obj_currency = CleanCurrency(TableCellSpan('currency'))
                obj_label = CleanText(TableCellSpan('label'))

                def obj_available_amount(self):
                    return MyDecimal(TableCellSpan('available_amount', default=NotAvailable), default=NotAvailable)(self)

                def obj_id(self):
                    td = TableCellSpan('label')(self)
                    return CleanText('./following-sibling::td[1]')(td[0])

                def obj_maturity_date(self):
                    if self.env['details']:
                        m = re.search('(\d{2}/\d{2}/\d{4})', CleanText('//div[@id="trPagePu"]//td[contains(., "Fin le") or contains(., "Date de remboursement")]', default=NotAvailable)(self.env['details'].doc))
                        if m and (m.group(1) != '00/00/0000'):
                            return MyDate().filter(m.group(1))
                    return NotAvailable

                def obj_subscription_date(self):
                    if self.env['details']:
                        m = re.search('(\d{2}/\d{2}/\d{4})', CleanText('//div[@id="trPagePu"]//td[contains(., "Début") or contains(., "Date de souscription")]', symbols=':', default=NotAvailable)(self.env['details'].doc))
                        if m and (m.group(1) != '00/00/0000'):
                            return MyDate().filter(m.group(1))
                    return NotAvailable

                def obj_duration(self):
                    if self.env['details']:
                        return MyDecimal(Regexp(CleanText(
                    '//div[@id="trPagePu"]//td[contains(., "Durée")]', default=NotAvailable), r' (\d+) ', default=NotAvailable), default=NotAvailable)(self.env['details'].doc)

                def obj_next_payment_date(self):
                    if self.env['details']:
                        return MyDate(Regexp(CleanText('//div[@id="trPagePu"]//td[contains(., "Prochaine")]'),
                                              r'(\d{2}/\d{2}/\d{4})', default=NotAvailable), default=NotAvailable)(self.env['details'].doc)

                def obj__perimeter(self):
                    return self.page.browser.current_perimeter

                def obj_rate(self):
                    if self.env['details']:
                        return MyDecimal('//div[@id="trPagePu"]//tr[contains(@class, "ligne")]/td[contains(., "Taux")]', default=NotAvailable)(self.env['details'].doc)

                def obj_type(self):
                    # TODO set back Account.TYPE_REVOLVING CREDIT
                    if Field('available_amount')(self):
                        return Account.TYPE_LOAN

                def condition(self):
                    return 'Billet financier' not in Field('label')(self)

                obj__form = None


class SavingsPage(AccountsPage):
    @pagination
    @method
    class iter_accounts(TableElement):
        head_xpath = '//table[@class="ca-table"]//tr/th'
        item_xpath = '//table[@class="ca-table"]//tr[contains(@class, "colcelligne")]'

        col_balance_op = 'En opération'
        col_balance_value = 'En valeur'
        col_currency = 'Devise'

        next_page = Link('//div[@class="btnsuiteliste"]/a[@class="btnsuiteliste"]', default=None)

        class item(ItemElement):
            klass = Account

            def condition(self):
                if not self.el.xpath('./td[contains(@class, "cel-texte")]'):
                    return False

                label = Field('label')(self)
                if any(label.startswith(prefix) for prefix in ('RENT', 'RENV', 'RACC')):
                    # RENTE account doesn't seem to have any balance or currency
                    return False

                return True

            obj_label = CleanText('./td[1]')
            obj_id = CleanText('./td[2]')

            def obj_balance(self):
                td = TableCellSpan('balance_op', default=NotAvailable)(self)
                if td:
                    return MyDecimal(td, default=NotAvailable)(self)
                return MyDecimal(TableCellSpan('balance_value'), default=NotAvailable)(self)

            obj_currency = CleanCurrency(TableCellSpan('currency'))

            def obj_type(self):
                type = self.page.TYPES.get(Field('label')(self), Account.TYPE_UNKNOWN)
                if not type:
                    return self.page.TYPES.get(CleanText('(./preceding-sibling::tr//td/h3)[last()]')(self).lower(), Account.TYPE_UNKNOWN)
                return type

            def obj_url(self):
                url = None
                origin = urlparse(self.page.url)
                link = Link('./td[2]//a', default=None)(self)
                # Sometimes there is no link.
                if (link and 'CATITRES' in link) or Field('type')(self) in (Account.TYPE_MARKET, Account.TYPE_PEA):
                    url = 'https://%s/stb/entreeBam?sessionSAG=%%s&stbpg=pagePU&site=CATITRES&typeaction=reroutage_aller'
                    url = url % origin.netloc

                if link:
                    if 'PREDICA' in link or 'CONTRAT' in link:
                        # account.type = Account.TYPE_LIFE_INSURANCE
                        url = 'https://%s/stb/entreeBam?sessionSAG=%%s&stbpg=pagePU&site=PREDICA2&' \
                              'typeaction=reroutage_aller&sdt=CONTRAT&parampartenaire=%s'
                        url = url % (origin.netloc, Field('id')(self))
                    # This aims to handle bgpi-gestionprivee.
                    elif 'javascript' in  link:
                        m = re.findall("'([^']*)'", link)
                        if len(m) == 3:
                            url = 'https://%s/stb/entreeBam?sessionSAG=%%s&stbpg=pagePU&typeaction=reroutage_aller&site=%s&sdt=%s&parampartenaire=%s'
                            url = url % (origin.netloc, m[0], m[1], m[2])
                    elif 'javascript' not in link and url is None:
                        url = urljoin(self.page.url, link.replace(' ', '%20'))
                        url = re.sub('sessionSAG=[^&]+', 'sessionSAG={0}', url)

                return url

            def obj__perimeter(self):
                return self.page.browser.current_perimeter

            def obj__liquidity_url(self):
                if 'COMPTE ESPECE PE' in CleanText('./td[1]//a/@onmouseover', default=None)(self):
                    return Link('./td[2]//a', default=None)(self)

            obj__form = None

            def validate(self, obj):
                # Skip accounts with '0' as id
                # They redirect to another cragr website
                # The accounts contained by this space already appear in the accounts list
                return Field('id')(self) != '0'


class TransactionsPage(MyLoggedPage, BasePage):
    def get_missing_balance(self):
        return CleanDecimal('//td[contains(text(), "Solde")]/following-sibling::td', replace_dots=True)(self.doc)

    def get_iban_url(self):
        for link in self.doc.xpath('//a[contains(text(), "RIB")] | //a[contains(text(), "IBAN")]'):
            m = re.search("\('([^']+)'", link.get('href', ''))
            if m:
                return m.group(1)

        return None

    def get_iban(self):
        s = ''
        for font in self.doc.xpath('(//td[font/b/text()="IBAN"])[1]/table//font'):
            s += CleanText().filter(font)
        return s

    def order_transactions(self):
        date = self.doc.xpath('//th[@scope="col"]/a[text()="Date"]')
        if len(date) < 1:
            return

        if 'active' in date[0].attrib.get('class', ''):
            return

        self.browser.location(date[0].attrib['href'])

    def get_next_url(self):
        links = self.doc.xpath('//span[@class="pager"]/a[@class="liennavigationcorpspage"]')
        if len(links) < 1:
            return None

        img = links[-1].find('img')
        if img.attrib.get('alt', '') == 'Page suivante':
            return links[-1].attrib['href']

        return None

    COL_DATE  = 0
    COL_TEXT  = 1
    COL_DEBIT = None
    COL_CREDIT = -1

    TYPES = {'Paiement Par Carte':          Transaction.TYPE_CARD,
             'Remise Carte':                Transaction.TYPE_CARD,
             'Prelevement Carte':           Transaction.TYPE_CARD_SUMMARY,
             'Retrait Au Distributeur':     Transaction.TYPE_WITHDRAWAL,
             'Frais':                       Transaction.TYPE_BANK,
             'Cotisation':                  Transaction.TYPE_BANK,
             'Virement Emis':               Transaction.TYPE_TRANSFER,
             'Virement':                    Transaction.TYPE_TRANSFER,
             'Cheque Emis':                 Transaction.TYPE_CHECK,
             'Remise De Cheque':            Transaction.TYPE_DEPOSIT,
             'Prelevement':                 Transaction.TYPE_ORDER,
             'Prelevt':                     Transaction.TYPE_ORDER,
             'Prelevmnt':                   Transaction.TYPE_ORDER,
             'Remboursement De Pret':       Transaction.TYPE_LOAN_PAYMENT,
            }

    def get_history(self, date_guesser):
        cleaner = CleanText().filter

        trs = self.doc.xpath('//table[@class="ca-table" and @summary]//tr')
        if trs:
            self.COL_TEXT += 1
        else:
            trs = self.doc.xpath('//table[@class="ca-table"]//tr')
        for tr in trs:
            parent = tr.getparent()
            while parent is not None and parent.tag != 'table':
                parent = parent.getparent()

            if parent.attrib.get('class', '') != 'ca-table':
                continue

            if tr.attrib.get('class', '') == 'tr-thead':
                heads = tr.findall('th')
                for i, head in enumerate(heads):
                    key = cleaner(head)
                    if key == u'Débit':
                        self.COL_DEBIT = i - len(heads)
                    if key == u'Crédit':
                        self.COL_CREDIT = i - len(heads)
                    if key == u'Libellé':
                        self.COL_TEXT = i

            if not tr.attrib.get('class', '').startswith('ligne-'):
                continue

            cols = tr.findall('td')

            # On loan accounts, there is a ca-table with a summary. Skip it.
            if tr.find('th') is not None or len(cols) < 3:
                continue

            # On PEL, skip summary.
            if len(cols[0].findall('span')) == 6:
                continue

            t = Transaction()

            col_text = cols[self.COL_TEXT]
            if len(col_text.xpath('.//br')) == 0:
                col_text = cols[self.COL_TEXT+1]

            raw = cleaner(col_text)
            # strip HTML comments if present
            for html_comment in col_text.xpath('.//comment()'):
                raw = raw.replace(html_comment.text, '')
            date = cleaner(cols[self.COL_DATE])
            credit = cleaner(cols[self.COL_CREDIT])
            if self.COL_DEBIT is not None:
                debit = cleaner(cols[self.COL_DEBIT])
            else:
                debit = ''

            if date.count('/') == 1:
                day, month = map(int, date.split('/', 1))
                t.date = date_guesser.guess_date(day, month)
            elif date.count('/') == 2:
                t.date = MyDate().filter(date)
            t.rdate = t.date
            t.raw = raw
            # Transactions DO NOT have a year. its only DD/MM. In some cases, if there is not enough
            # transactions in the account, a transaction dating from last year has it date incorrectly
            # guessed (with the date_guesser/ to avoid this we had to reduce de data_guesser.date_max_bump
            # to 2 in order to prevenmt such problems, but only for savings accounts. It is not perfect,
            # hence the assertion to make sure we aren't returning a future date that is more than 2 days forward.
            assert datetime(year=t.date.year, month=t.date.month, day=t.date.day) + relativedelta(date_guesser.date_max_bump.days) < datetime.today()

            # On some accounts' history page, there is a <font> tag in columns.
            if col_text.find('font') is not None:
                col_text = col_text.find('font')

            t.category = unicode(col_text.text.strip())
            t.label = re.sub('(.*)  (.*)', r'\2', t.category).strip()

            br = col_text.find('br')
            if br is not None:
                sub_label = br.tail
            if br is not None and sub_label is not None:
                junk_ratio = len(re.findall('[^\w\s]', sub_label)) / float(len(sub_label))
                nums_ratio = len(re.findall('\d', t.label)) / float(len(t.label))
                if len(t.label) < 3 or t.label == t.category or junk_ratio < nums_ratio:
                    t.label = unicode(sub_label.strip())
            # Sometimes, the category contains the label, even if there is another line with it again.
            t.category = re.sub('(.*)  .*', r'\1', t.category).strip()

            t.type = self.TYPES.get(t.category, t.TYPE_UNKNOWN)

            # Sometimes, the deffered card summary transaction can be only identified from the label
            if 'Depenses Carte' in t.label:
                t.type = t.TYPE_CARD_SUMMARY

            # Parse operation date in label (for card transactions for example)
            m = re.match('(?P<text>.*) (?P<dd>[0-3]\d)/(?P<mm>[0-1]\d)$', t.label)
            if not m:
                m = re.match('^(?P<dd>[0-3]\d)/(?P<mm>[0-1]\d) (?P<text>.*)$', t.label)
            if m:
                if t.type in (t.TYPE_CARD, t.TYPE_WITHDRAWAL):
                    t.rdate = date_guesser.guess_date(int(m.groupdict()['dd']), int(m.groupdict()['mm']), change_current_date=False)
                t.label = m.groupdict()['text'].strip()

            # Strip city or other useless information from label.
            t.label = re.sub('(.*)  .*', r'\1', t.label).strip()
            t.set_amount(credit, debit)
            yield t


class HistoryPostPage(CollectePageMixin, TransactionsPage):
    IS_HERE_TEXT = ('Consultation des comptes', 'Relevé')


class NoFixedDepositPage(MyLoggedPage, BasePage):
    def is_here(self):
        return 'Vous ne disposez pas de DAT' in CleanText('//div[@class="blc-choix-wrap-valid"]/table/tr/td')(self.doc)


class UnavailablePage(CollectePageMixin, BasePage):
    def is_here(self):
        return bool(self.get_error())


class AutoEncodingMixin(object):
    def build_doc(self, data):
        try:
            data.decode('utf-8')
            self.forced_encoding = 'utf-8'
        except UnicodeDecodeError:
            self.forced_encoding = 'iso8859-15'
        return super(AutoEncodingMixin, self).build_doc(data)


class MarketPage(MyLoggedPage, AutoEncodingMixin, BasePage):
    COL_ID = 1
    COL_QUANTITY = 2
    COL_UNITVALUE = 3
    COL_VALUATION = 4
    COL_UNITPRICE = 5
    COL_DIFF = 6

    def iter_investment(self):
        for line in self.doc.xpath('//table[contains(@class, "ca-data-table")]/descendant::tr[count(td)>=7]'):
            for sub in line.xpath('./td[@class="info-produit"]'):
                sub.drop_tree()
            cells = line.findall('td')

            if cells[self.COL_ID].find('div/a') is None:
                continue
            inv = Investment()
            inv.label = unicode(cells[self.COL_ID].find('div/a').text.strip())
            inv.code = cells[self.COL_ID].find('div/br').tail.strip().split(' ')[0].split(u'\xa0')[0].split(u'\xc2\xa0')[0]
            inv.quantity = self.parse_decimal(cells[self.COL_QUANTITY].find('span').text)
            inv.valuation = self.parse_decimal(cells[self.COL_VALUATION].text)
            inv.diff = self.parse_decimal(cells[self.COL_DIFF].text_content())
            if "%" in cells[self.COL_UNITPRICE].text and "%" in cells[self.COL_UNITVALUE].text:
                inv.unitvalue = inv.valuation / inv.quantity
                inv.unitprice = (inv.valuation - inv.diff) / inv.quantity
            else:
                inv.unitprice = self.parse_decimal(re.search('([^(]+)', cells[self.COL_UNITPRICE].text).group(1))
                inv.unitvalue = self.parse_decimal(cells[self.COL_UNITVALUE].text)
            date = cells[self.COL_UNITVALUE].find('span').text
            if ':' in date:
                inv.vdate = ddate.today()
            else:
                day, month = [int(x) for x in date.split('/')][:2]
                date_guesser = LinearDateGuesser()
                inv.vdate = date_guesser.guess_date(day, month)

            yield inv

    def parse_decimal(self, value):
        v = value.strip()
        if v == '-' or v == '' or v == '_':
            return NotAvailable
        if '.' in value and not ',' in value:
            return CleanDecimal().filter(value)
        return MyDecimal().filter(value)

    # This method is used to fetch the PEA balance without liquidities present on the DAV PEA:
    def get_pea_balance(self):
        return CleanDecimal('//tr[td[text()="Valorisation titres"]]/td/span', replace_dots=True)(self.doc)


class MarketHomePage(MarketPage):
    COL_ID_LABEL = 1
    COL_VALUATION = 5

    def on_load(self):
        action_needed_msg = CleanText('//div[contains(text(), "Afin de finaliser le paramétrage de votre environnement Bourse")]', replace=[('Enregistrer', '')])(self.doc)
        if action_needed_msg:
            raise ActionNeeded(action_needed_msg)

    @method
    class get_list(TableElement):
        item_xpath = '//table[has-class("tableau_comptes_details")]//tr[td[2]]'
        head_xpath = '//table[has-class("tableau_comptes_details")]//tr/th'

        col_label = u'Comptes'
        col_balance = re.compile(u'Valorisation')

        class item(ItemElement):
            klass = Account

            condition = lambda self: Field('id')(self)

            def obj_id(self):
                return CleanText(default=None).filter(TableCell('label')(self)[0].xpath('./div[2]'))

            def obj_label(self):
                return CleanText(default=None).filter(TableCell('label')(self)[0].xpath('./div/b'))

            obj_balance = MyDecimal(TableCell('balance'))


class LifeInsurancePage(MarketPage):
    COL_ID = 0
    COL_QUANTITY = 3
    COL_UNITVALUE = 1
    COL_VDATE = 2
    COL_VALUATION = 4
    COL_PSHARE = 5

    def go_on_detail(self, account_id):
        # Sometimes this page is a synthesis, so we need to go on detail.
        if len(self.doc.xpath(u'//h1[contains(text(), "Synthèse de vos contrats d\'assurance vie, de capitalisation et de prévoyance")]')) == 1:
            form = self.get_form(name='frm_fwk')
            form['ID_CNT_CAR'] = account_id
            form['fwkaction'] = 'Enchainer'
            form['fwkcodeaction'] = 'Executer'
            form['puCible'] = 'SEPPU'
            form.submit()
            self.browser.location('https://assurance-personnes.credit-agricole.fr/filiale/entreeBam?sessionSAG=%s&stbpg=pagePU&act=SEPPU&stbzn=bnt&actCrt=SEPPU' % self.browser.sag)

    def has_liquidities(self):
        return bool(self.doc.xpath('//span[contains(text(), "de versement restant")]'))

    def get_liquidities(self):
        return CleanDecimal(Regexp(CleanText('//span[contains(text(), "de versement restant")]'),
                                  "dont (.*) de versement restant"), replace_dots=True)(self.doc)

    def iter_investment(self):

        for line in self.doc.xpath('//table[@summary and count(descendant::td) > 1]/tbody/tr'):
            cells = line.findall('td')
            if len(cells) < 5:
                continue

            inv = Investment()
            inv.label = unicode(cells[self.COL_ID].text_content().strip())
            a = cells[self.COL_ID].find('a')
            if a is not None:
                try:
                    inv.code = a.attrib['id'].split(' ')[0].split(u'\xa0')[0].split(u'\xc2\xa0')[0]
                except KeyError:
                    #For "Mandat d'arbitrage" which is a recapitulatif of more investement
                    continue
            else:
                inv.code = NotAvailable
            inv.quantity = self.parse_decimal(cells[self.COL_QUANTITY].text_content()) or NotAvailable
            inv.unitvalue = self.parse_decimal(cells[self.COL_UNITVALUE].text_content()) or NotAvailable
            inv.valuation = self.parse_decimal(cells[self.COL_VALUATION].text_content())
            inv.unitprice = NotAvailable
            inv.diff = NotAvailable
            # clean text before filtering the date
            inv.vdate = Date(CleanText('.'), dayfirst=True, default=NotAvailable)(cells[self.COL_VDATE])
            inv.portfolio_share = self.parse_decimal(cells[self.COL_PSHARE].text_content()) / 100

            yield inv


class AdvisorPage(MyLoggedPage, BasePage):
    def get_codeperimetre(self):
        script = CleanText('//script[contains(text(), "codePerimetre")]', default=None)(self.doc)
        if script:
            return Regexp(pattern=r'codePerimetre.+?(\d+).+?codeAgence.+?(\d+)', template='\\1-\\2').filter(script)

    @method
    class get_advisor(ItemElement):
        klass = Advisor

        obj_name = CleanText('//span[@class="c1"]')
        obj_email = CleanText('//span[@id="emailCons"]')
        obj_phone = Regexp(CleanText('//span[@class="c2"]', symbols=' ', default=""), '(\d+)', default=NotAvailable)
        obj_agency = CleanText('//span[@class="a1"]')

    def iter_numbers(self):
        for p in self.doc.xpath(u'//fieldset/div/p[not(contains(text(), "TTC"))]'):
            phone = None
            if p.find('b') is not None:
                phone = ''.join(p.find('b').text_content().split('.'))
            else:
                m = re.search('(?=\d)([\d\s.]+)', p.text_content().strip())
                if m:
                    phone = ''.join(m.group(1).split())
            if not phone or len(phone) != 10:
                continue

            adv = Advisor()
            adv.name = unicode(re.search('([^:]+)', p.find('span').text).group(1).strip())
            if adv.name.startswith('Pour toute '):
                adv.name = u"Fil général"
            adv.phone = unicode(phone)
            if "bourse" in adv.name:
                adv.role = u"wealth"
            [setattr(adv, k, NotAvailable) for k in ['email', 'mobile', 'fax', 'agency', 'address']]
            yield adv


class ProfilePage(MyLoggedPage, BasePage):
    def get_error(self):
        return CleanText('//div[@id="trPagePu"]//h1[text()="Fonctionnalité Indisponible"]/following::p')(self.doc)

    @method
    class get_profile(ItemElement):
        klass = Profile

        obj_email = Regexp(CleanText('//font/b/script[contains(text(), "Mail")]', default=""), '\'([^\']+)', default=NotAvailable)

        def obj_address(self):
            address = ""
            for tr in self.page.doc.xpath('//tr[td[contains(text(), "Adresse")]]/following-sibling::tr[position() < 4]'):
                address += " " + CleanText('./td[last()]')(tr).strip()
            return ' '.join(address.split()) or NotAvailable

        def obj_name(self):
            name = CleanText('//span[contains(text(), "Espace Titulaire")]', default=None)(self)
            if name and not "particuliers" in name:
                return ''.join(name.split(':')[1:]).strip()
            pattern = u'//td[contains(text(), "%s")]/following-sibling::td[1]'
            return Format('%s %s', CleanText(pattern % "Nom"), CleanText(pattern % "Prénom"))(self)


class BGPIPage(MarketPage):
    COL_ID = 0
    COL_QUANTITY = 1
    COL_UNITPRICE = 2
    COL_UNITVALUE = 3
    COL_VALUATION = 4
    COL_PORTFOLIO = 5
    COL_DIFF = 6

    def iter_investment(self):
        for line in self.doc.xpath('//table[contains(@class, "PuTableauLarge")]/tr[contains(@class, "PuLigne")]'):
            cells = line.findall('td')
            inv = Investment()

            inv.label = unicode(cells[self.COL_ID].find('span').text_content().strip())
            a = cells[self.COL_ID].find('a')
            if a is not None:
                inv.code = unicode(a.text_content().strip())
            else:
                inv.code = NotAvailable

            inv.quantity = self.parse_decimal(cells[self.COL_QUANTITY].text_content())
            if 5 <= len(cells) <= 7:
                inv.unitvalue = self.parse_decimal(cells[2].text_content())
                inv.valuation = self.parse_decimal(cells[3].text_content())
                inv.portfolio_share = self.parse_decimal(cells[4].text_content())/100
            else:
                inv.unitvalue = self.parse_decimal(cells[self.COL_UNITVALUE].text_content())
                inv.valuation = self.parse_decimal(cells[self.COL_VALUATION].text_content())
                inv.unitprice = self.parse_decimal(cells[self.COL_UNITPRICE].text_content())
                inv.diff = self.parse_decimal(cells[self.COL_DIFF].text_content())
                inv.portfolio_share = self.parse_decimal(cells[self.COL_PORTFOLIO].text_content())/100

            yield inv

    def get_li_details(self, _id):
        label = CleanText('//tr[td[text()="%s"]]/td/a' % _id)(self.doc)
        balance = CleanDecimal('//tr[td[text()="%s"]]/td[@class="cel-num cel-important"]' % _id, replace_dots=True)(self.doc)
        return label, balance

    def go_on(self, link):
        origin = urlparse(self.url)
        self.browser.location('https://%s%s' % (origin.netloc, link))
        return True

    def go_detail(self):
        link = self.doc.xpath('.//a[contains(text(), "Détail")]')
        return self.go_on(link[0].attrib['href']) if link else False

    def get_params(self, _id):
        option = Attr('//select[@class="BntSelectOption"]/option[text()="%s"]' % _id, 'value', default=None)(self.doc)
        if option:
            params = {'id': option}
            return params
        return None

    def go_back(self):
        self.go_on(self.doc.xpath(u'.//a[contains(text(), "Retour à mes comptes")]')[0].attrib['href'])
        form = self.browser.page.get_form(name='formulaire')
        form.submit()

    def cgu_needed(self):
        return bool(CleanText(u'//h1[contains(text(), "Conditions Générales d\'utilisation des Services en Ligne")]')(self.doc))


class PredicaRedirectPage(MyLoggedPage, BasePage):
    pass


class TransferInit(MyLoggedPage, BasePage):
    def iter_emitters(self):
        items = self.doc.xpath('//select[@name="VIR_VIR1_FR3_LE"]/option')
        return self.parse_recipients(items, assume_internal=True)

    def iter_recipients(self):
        items = self.doc.xpath('//select[@name="VIR_VIR1_FR3_LB"]/option')
        return self.parse_recipients(items)

    def parse_recipients(self, items, assume_internal=False):
        for opt in items:
            lines = get_text_lines(opt)

            if opt.attrib['value'].startswith('I') or assume_internal:
                for n, line in enumerate(lines):
                    if line.strip().startswith('n°'):
                        rcpt = Recipient()
                        rcpt._index = opt.attrib['value']
                        rcpt._raw_label = ' '.join(lines)
                        rcpt.category = 'Interne'
                        rcpt.id = CleanText().filter(line[2:].strip())
                        # we don't have iban here, use account number
                        rcpt.label = ' '.join(lines[:n])
                        rcpt.currency = Currency.get_currency(lines[-1])
                        rcpt.enabled_at = datetime.now().replace(microsecond=0)
                        yield rcpt
                        break
            elif opt.attrib['value'].startswith('E'):
                if len(lines) > 1:
                    # In some cases we observed beneficiaries without label, we skip them
                    rcpt = Recipient()
                    rcpt._index = opt.attrib['value']
                    rcpt._raw_label = ' '.join(lines)
                    rcpt.category = 'Externe'
                    rcpt.label = lines[0]
                    rcpt.iban = lines[1].upper()
                    rcpt.id = rcpt.iban
                    rcpt.enabled_at = datetime.now().replace(microsecond=0)
                    yield rcpt
                else:
                    self.logger.warning('The recipient associated with the iban %s has got no label' % lines[0])

    def submit_accounts(self, account_id, recipient_id, amount, currency):
        emitters = [rcpt for rcpt in self.iter_emitters() if rcpt.id == account_id and not rcpt.iban]
        if len(emitters) != 1:
            raise TransferError('Could not find emitter %r' % account_id)
        recipients = [rcpt for rcpt in self.iter_recipients() if rcpt.id and rcpt.id == recipient_id]
        # for recipient with same IBAN, first matched recipient is the default value
        if len(recipients) < 1:
            raise TransferError('Could not find recipient %r' % recipient_id)

        form = self.get_form(name='frm_fwk')
        assert amount > 0
        amount = str(amount.quantize(Decimal('0.00')))
        form['T3SEF_MTT_EURO'], form['T3SEF_MTT_CENT'] = amount.split('.')
        form['VIR_VIR1_FR3_LE'] = emitters[0]._index
        form['VIR_VIR1_FR3_LB'] = recipients[0]._index
        form['DEVISE'] = currency or emitters[0].currency
        form['VIR_VIR1_FR3_LE_HID'] = emitters[0]._raw_label
        form['VIR_VIR1_FR3_LB_HID'] = recipients[0]._raw_label
        form['fwkaction'] = 'Confirmer' # mandatory
        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def url_list_recipients(self):
        return CleanText(u'(//a[contains(text(),"Liste des bénéficiaires")])[1]/@href')(self.doc)

    def add_recipient_is_allowed(self):
        return bool(self.doc.xpath('//a[text()="+ Saisir un autre compte bénéficiaire"]') or self.doc.xpath('//a[contains(text(),"Liste des bénéficiaires")]'))

    def url_add_recipient(self):
        link = Link('//a[text()="+ Saisir un autre compte bénéficiaire"]')(self.doc)
        return link + '&IDENT=LI_VIR_RIB1&VIR_VIR1_FR3_LE=0&T3SEF_MTT_EURO=&T3SEF_MTT_CENT=&VICrt_REFERENCE='


class RecipientListPage(MyLoggedPage, BasePage):
    def url_add_recipient(self):
        return CleanText(u'//a[contains(text(),"Ajouter un compte destinataire")]/@href')(self.doc)


class RecipientAddingMixin(object):
    def submit_recipient(self, label, iban):
        try:
            form = self.get_form(name='frm_fwk')
        except FormNotFound:
            assert False, 'An error occurred before sending recipient'

        form['NOM_BENEF'] = label
        for i in range(9):
            form['CIBAN%d' % (i + 1)] = iban[i * 4:(i + 1) * 4]
        form['fwkaction'] = 'VerifCodeIBAN'
        form['fwkcodeaction'] = 'Executer'
        form.submit()


class TransferPage(RecipientAddingMixin, CollectePageMixin, MyLoggedPage, BasePage):
    IS_HERE_TEXT = 'Virement'

    ### for transfers
    def get_step(self):
        return CleanText('//div[@id="etapes"]//li[has-class("encours")]')(self.doc)

    def is_sent(self):
        return self.get_step().startswith('Récapitulatif')

    def is_confirm(self):
        return self.get_step().startswith('Confirmation')

    def is_reason(self):
        return self.get_step().startswith('Informations complémentaires')

    def get_transfer(self):
        transfer = Transfer()

        # FIXME all will probably fail if an account has a user-chosen label with "IBAN :" or "n°"

        amount_xpath = '//fieldset//p[has-class("montant")]'
        transfer.amount = MyDecimal(amount_xpath)(self.doc)
        transfer.currency = CleanCurrency(amount_xpath)(self.doc)

        if self.is_sent():
            transfer.account_id = Regexp(CleanText('//p[@class="nomarge"][span[contains(text(),'
                                                   '"Compte émetteur")]]/text()'),
                                         r'n°(\d+)')(self.doc)

            base = CleanText('//fieldset//table[.//span[contains(text(), "Compte bénéficiaire")]]'
                             '//td[contains(text(),"n°") or contains(text(),"IBAN :")]//text()', newlines=False)(self.doc)
            transfer.recipient_id = Regexp(None, r'IBAN : ([^\n]+)|n°(\d+)').filter(base)
            transfer.recipient_id = transfer.recipient_id.replace(' ', '')
            if 'IBAN' in base:
                transfer.recipient_iban = transfer.recipient_id

            transfer.exec_date = MyDate(CleanText('//p[@class="nomarge"][span[contains(text(), "Date de l\'ordre")]]/text()'))(self.doc)
        else:
            transfer.account_id = Regexp(CleanText('//fieldset[.//h3[contains(text(), "Compte émetteur")]]//p'),
                                         r'n°(\d+)')(self.doc)

            base = CleanText('//fieldset[.//h3[contains(text(), "Compte bénéficiaire")]]//text()',
                             newlines=False)(self.doc)
            transfer.recipient_id = Regexp(None, r'IBAN : ([^\n]+)|n°(\d+)').filter(base)
            transfer.recipient_id = transfer.recipient_id.replace(' ', '')
            if 'IBAN' in base:
                transfer.recipient_iban = transfer.recipient_id

            transfer.exec_date = MyDate(CleanText('//fieldset//p[span[contains(text(), "Virement unique le :")]]/text()'))(self.doc)

        transfer.label = CleanText('//fieldset//p[span[contains(text(), "Référence opération")]]')(self.doc)
        transfer.label = re.sub(r'^Référence opération(?:\s*):', '', transfer.label).strip()

        return transfer

    def submit_more(self, label, date=None):
        if date is None:
            date = ddate.today()

        form = self.get_form(name='frm_fwk')
        form['VICrt_CDDOOR'] = label
        form['VICrtU_DATEVRT_JJ'] = date.strftime('%d')
        form['VICrtU_DATEVRT_MM'] = date.strftime('%m')
        form['VICrtU_DATEVRT_AAAA'] = date.strftime('%Y')
        form['DATEC'] = date.strftime('%d/%m/%Y')
        form['PERIODE'] = 'U'
        form['fwkaction'] = 'Confirmer'
        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def submit_confirm(self):
        form = self.get_form(name='frm_fwk')
        form['fwkaction'] = 'Confirmer'
        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def on_load(self):
        super(TransferPage, self).on_load()
        # warning: the "service indisponible" message (not catched here) is not a real BrowserUnavailable
        err = CleanText('//form//div[has-class("blc-choix-erreur")]//p', default='')(self.doc)
        if err:
            raise TransferBankError(message=err)

    ### add a recipient by faking a transfer
    def confirm_recipient(self):
        # pretend to make a transfer
        form = self.get_form(name='frm_fwk')
        form['AJOUT_BENEF_CHECK'] = 'on'
        form['fwkcodeaction'] = 'Executer'
        form['fwkaction'] = 'Suite'
        form['T3SEF_MTT_EURO'] = '1'
        form['DEVISE'] = 'EUR'
        form.submit()

    def check_error(self):
        # this is for transfer error, it's not a `AddRecipientBankError` but a `TransferBankError`

        msg = CleanText('//tr[@bgcolor="#C74545"]', default='')(self.doc) # there is no id, class or anything...
        if msg:
            raise TransferBankError(message=msg)

    def check_recipient_error(self):
        # this is a copy-paste from RecipientMiscPage, i can't test if it works on this page...
        # this is for add recipient by initiate transfer

        msg = CleanText('//tr[@bgcolor="#C74545"]', default='')(self.doc) # there is no id, class or anything...
        if msg:
            raise AddRecipientBankError(message=msg)


class RecipientMiscPage(RecipientAddingMixin, CollectePageMixin, MyLoggedPage, BasePage):
    IS_HERE_TEXT = 'Liste des comptes bénéficiaires'

    ### for adding recipients
    def send_sms(self):
        form = self.get_form(name='frm_fwk')

        assert 'code' not in form
        form['fwkaction'] = 'DemandeCodeSMSVerifID'
        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def get_sms_error(self):
        return CleanText('//div[@class="blc-choix-wrap-erreur"]')(self.doc)

    def confirm_recipient(self):
        try:
            form = self.get_form(name='frm_fwk')
        except FormNotFound:
            assert False, 'An error occurred before finishing adding recipient'

        form['fwkaction'] = 'ConfirmerAjout'
        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def check_recipient_error(self):
        msg = CleanText('//tr[@bgcolor="#C74545"]', default='')(self.doc) # there is no id, class or anything...
        if msg:
            raise AddRecipientBankError(message=msg)

    def get_iban_col(self):
        for index, td in enumerate(self.doc.xpath('//table[starts-with(@summary,"Nom et IBAN")]//th')):
            if 'Numéro de compte' in CleanText('.')(td):
                # index start at 0
                return index + 1

    def find_recipient(self, iban):
        iban = iban.upper()
        iban_col = self.get_iban_col()

        for tr in self.doc.xpath('//table[starts-with(@summary,"Nom et IBAN")]/tbody/tr'):
            iban_text = re.sub(r'\s', '', CleanText('./td[%s]' % iban_col)(tr))
            if iban_text.upper() == 'IBAN:%s' % iban:
                res = Recipient()
                res.iban = iban
                res.id = iban
                res.label = CleanText('./td[%s]' % (iban_col-1))(tr)
                return res


class RecipientPage(MyLoggedPage, BasePage):
    def can_send_code(self):
        form = self.get_form(name='frm_fwk')
        return 'code' in form

    def send_sms(self):
        form = self.get_form(name='frm_fwk')

        if 'code' in form:
            # a code is still pending, ask a new one
            form['fwkaction'] = 'NouvelleDemandeCodeSMS'
            form['fwkcodeaction'] = 'Executer'
            new_page = form.submit().page
            assert isinstance(new_page, TransferPage) or isinstance(new_page, SendSMSPage)
            return new_page.send_sms()
        else:
            form['fwkaction'] = 'DemandeCodeSMSVerifID'

        form['fwkcodeaction'] = 'Executer'
        form.submit()

    def submit_code(self, code):
        form = self.get_form(name='frm_fwk')
        form['fwkaction'] = 'Confirmer'
        form['fwkcodeaction'] = 'Executer'
        form['code'] = code
        form.submit()


class SendSMSPage(MyLoggedPage, CollectePageMixin, BasePage):
    IS_HERE_TEXT = 'Authentification par sms - demande'

    def on_load(self):
        # if the otp is incorrect
        error_msg = CleanText('//div[has-class("blc-choix-erreur")]//span')(self.doc)
        if error_msg:
            raise AddRecipientBankError(message=error_msg)

    def send_sms(self):
        # when a code is still pending
        # resend sms to validate recipient
        form = self.get_form(name='frm_fwk')
        form['fwkaction'] = 'DemandeCodeSMSVerifID'
        form['fwkcodeaction'] = 'Executer'
        form.submit()


def get_text_lines(el):
    lines = [re.sub(r'\s+', ' ', line).strip() for line in el.text_content().split('\n')]
    return [l for l in lines if l]


class DeferredCardsPage(CollectePageMixin, CardsPage):
    IS_HERE_TEXT = (u'Cartes - détail', 'Cartes')
