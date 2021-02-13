# -*- coding: utf-8 -*-

# Copyright(C) 2016      Edouard Lambert
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
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta

from weboob.exceptions import BrowserUnavailable
from weboob.browser.pages import HTMLPage, PDFPage, LoggedPage, AbstractPage
from weboob.browser.elements import ItemElement, TableElement, method
from weboob.browser.filters.standard import CleanText, CleanDecimal, Date, Regexp, Field, Env, Currency
from weboob.browser.filters.html import Attr, Link, TableCell
from weboob.capabilities.bank import Account, AccountOwnership
from weboob.capabilities.wealth import Investment
from weboob.tools.capabilities.bank.iban import is_iban_valid
from weboob.capabilities.base import NotAvailable, empty
from weboob.capabilities.profile import Person
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.compat import unicode
from weboob.tools.pdf import extract_text


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True, default=NotAvailable)
    return CleanDecimal(*args, **kwargs)


class UnavailablePage(HTMLPage):
    def on_load(self):
        raise BrowserUnavailable()


class MyHTMLPage(HTMLPage):
    def get_view_state(self):
        return self.doc.xpath('//input[@name="javax.faces.ViewState"]')[0].attrib['value']

    def is_password_expired(self):
        return len(self.doc.xpath('//div[@id="popup_client_modifier_code_confidentiel"]'))

    def parse_number(self, number):
        # For some client they randomly displayed 4,115.00 and 4 115,00.
        # Browser is waiting for for 4 115,00 so we format the number to match this.
        if '.' in number and len(number.split('.')[-1]) == 2:
            return number.replace(',', ' ').replace('.', ',')
        return number

    def js2args(self, s):
        args = {}
        # For example:
        # noDoubleClic(this);;return oamSubmitForm('idPanorama','idPanorama:tableaux-comptes-courant-titre:0:tableaux-comptes-courant-titre-cartes:0:_idJsp321',null,[['paramCodeProduit','9'],['paramNumContrat','12234'],['paramNumCompte','12345678901'],['paramNumComptePassage','1234567890123456']]);
        for sub in re.findall("\['([^']+)','([^']+)'\]", s):
            args[sub[0]] = sub[1]

        sub = re.search('oamSubmitForm.+?,\'([^:]+).([^\']+)', s)
        args['%s:_idcl' % sub.group(1)] = "%s:%s" % (sub.group(1), sub.group(2))
        args['%s_SUBMIT' % sub.group(1)] = 1
        args['_form_name'] = sub.group(1)  # for weboob only

        return args


class AccountsPage(LoggedPage, MyHTMLPage):
    ACCOUNT_TYPES = OrderedDict((
        ('visa', Account.TYPE_CARD),
        ('pea', Account.TYPE_PEA),
        ('valorisation', Account.TYPE_MARKET),
        ('courant-titre', Account.TYPE_CHECKING),
        ('courant', Account.TYPE_CHECKING),
        ('livret', Account.TYPE_SAVINGS),
        ('ldd', Account.TYPE_SAVINGS),
        ('pel', Account.TYPE_SAVINGS),
        ('cel', Account.TYPE_SAVINGS),
        ('titres', Account.TYPE_MARKET),
    ))

    def get_tabs(self):
        links = self.doc.xpath('//strong[text()="Mes Comptes"]/following-sibling::ul//a/@href')
        links.insert(0, "-comptes")
        return list(set([re.findall('-([a-z]+)', x)[0] for x in links]))

    def has_accounts(self):
        return self.doc.xpath('//table[not(@id) and contains(@class, "table-produit")]')

    def get_pages(self, tab):
        pages = []
        pages_args = []
        if len(self.has_accounts()) == 0:
            table_xpath = '//table[contains(@id, "%s")]' % tab
            links = self.doc.xpath('%s//td[1]/a[@onclick and contains(@onclick, "noDoubleClic")]' % table_xpath)
            if len(links) > 0:
                form_xpath = '%s/ancestor::form[1]' % table_xpath
                form = self.get_form(form_xpath, submit='%s//input[1]' % form_xpath)
                data = {k: v for k, v in dict(form).items() if v}
                for link in links:
                    d = self.js2args(link.attrib['onclick'])
                    d.update(data)
                    pages.append(self.browser.location(form.url, data=d).page)
                    pages_args.append(d)
        else:
            pages.append(self)
            pages_args.append(None)
        return zip(pages, pages_args)

    def get_list(self):
        for table in self.has_accounts():
            tds = table.xpath('./tbody/tr')[0].findall('td')
            if len(tds) < 3:
                if tds[0].text_content() == 'Prêt Personnel':

                    account = Account()
                    args = self.js2args(table.xpath('.//a')[0].attrib['onclick'])
                    account._args = args
                    account.label = CleanText().filter(tds[0].xpath('./ancestor::table[has-class("tableaux-pret-personnel")]/caption'))
                    account.id = account.label.split()[-1] + args['paramNumContrat']
                    account.number = account.id
                    loan_details = self.browser.open('/webapp/axabanque/jsp/panorama.faces', data=args).page
                    # Need to go back on home page after open
                    self.browser.bank_accounts.open()
                    account.balance = loan_details.get_loan_balance()
                    account.currency = loan_details.get_loan_currency()
                    account.ownership = loan_details.get_loan_ownership()
                    # Skip loans without any balance (already fully reimbursed)
                    if empty(account.balance):
                        continue
                    account.type = Account.TYPE_LOAN
                    account._acctype = "bank"
                    account._hasinv = False
                    account._is_debit_card = False
                    yield account

                continue

            boxes = table.xpath('./tbody//tr[not(.//strong[contains(text(), "Total")])]')
            foot = table.xpath('./tfoot//tr')

            for box in boxes:
                account = Account()
                account._url = None

                if len(box.xpath('.//a')) != 0 and 'onclick' in box.xpath('.//a')[0].attrib:
                    args = self.js2args(box.xpath('.//a')[0].attrib['onclick'])
                    account.label = '%s %s' % (table.xpath('./caption')[0].text.strip(), box.xpath('.//a')[0].text.strip())
                elif len(foot[0].xpath('.//a')) != 0 and 'onclick' in foot[0].xpath('.//a')[0].attrib:
                    args = self.js2args(foot[0].xpath('.//a')[0].attrib['onclick'])
                    account.label = table.xpath('./caption')[0].text.strip()
                    # Adding 'Valorisation' to the account label in order to differentiate it
                    # from the card and checking account associate to the './caption'
                    if 'Valorisation' not in account.label and len(box.xpath('./td[contains(text(), "Valorisation")]')):
                        account.label = '%s Valorisation Titres' % CleanText('./caption')(table)
                else:
                    continue

                self.logger.debug('Args: %r' % args)
                if 'paramNumCompte' not in args:
                    # The displaying of life insurances is very different from the other
                    if args.get('idPanorama:_idcl').split(":")[1] == 'tableaux-direct-solution-vie':
                        account_details = self.browser.open("#", data=args)
                        scripts = account_details.page.doc.xpath('//script[@type="text/javascript"]/text()')
                        script = list(filter(lambda x: "src" in x, scripts))[0]
                        iframe_url = re.search("src:(.*),", script).group()[6:-2]
                        account_details_iframe = self.browser.open(iframe_url, data=args)
                        account.id = CleanText('//span[contains(@id,"NumeroContrat")]/text()')(account_details_iframe.page.doc)
                        account.number = account.id
                        account._url = iframe_url
                        account.type = account.TYPE_LIFE_INSURANCE
                        account.balance = MyDecimal('//span[contains(@id,"MontantEpargne")]/text()')(account_details_iframe.page.doc)
                        account._acctype = "bank"
                        account._is_debit_card = False
                    else:
                        try:
                            label = unicode(table.xpath('./caption')[0].text.strip())
                        except Exception:
                            label = 'Unable to determine'
                        self.logger.warning('Unable to get account ID for %r' % label)
                        continue

                if account.type != account.TYPE_LIFE_INSURANCE:
                    # get accounts type
                    account_type_str = ''
                    for l in table.attrib['class'].split(' '):
                        if 'tableaux-comptes-' in l:
                            account_type_str = l[len('tableaux-comptes-'):].lower()
                            break

                    account.type = Account.TYPE_UNKNOWN
                    for pattern, type in self.ACCOUNT_TYPES.items():
                        if pattern in account_type_str or pattern in account.label.lower():
                            account.type = type
                            break

                    # get accounts id
                    account.id = args['paramNumCompte'] + args.get('paramNumContrat', '')

                    if 'Visa' in account.label:
                        account.number = Regexp(CleanText('./td[contains(@class,"libelle")]', replace=[(' ', ''), ('x', 'X')]), r'(X{12}\d{4})')(box)
                        account.id += Regexp(CleanText('./td[contains(@class,"libelle")]'), r'(\d+)')(box)

                    if 'Valorisation' in account.label or 'Liquidités' in account.label:
                        account.id += args[next(k for k in args.keys() if '_idcl' in k)].split('Jsp')[-1]
                        account.number = account.id

                    # get accounts balance
                    try:
                        balance_value = CleanText('.//td[has-class("montant")]')(box)

                        # skip debit card
                        # some cards don't have information in balance tab, skip them
                        if balance_value == 'Débit immédiat' or balance_value == '':
                            account._is_debit_card = True
                        else:
                            account._is_debit_card = False

                        account.balance = Decimal(FrenchTransaction.clean_amount(self.parse_number(balance_value)))
                        if account.type == Account.TYPE_CARD:
                            account.coming = account.balance
                            account.balance = Decimal(0)

                    except InvalidOperation:
                        # The account doesn't have a amount
                        pass

                    account._url = self.doc.xpath('//form[contains(@action, "panorama")]/@action')[0]
                    account._acctype = "bank"
                    account._owner = CleanText('./td[has-class("libelle")]')(box)

                # get accounts currency
                currency_title = table.xpath('./thead//th[@class="montant"]')[0].text.strip()
                m = re.match('Montant \((\w+)\)', currency_title)
                if not m:
                    self.logger.warning('Unable to parse currency %r' % currency_title)
                else:
                    account.currency = account.get_currency(m.group(1))

                account._args = args
                account._hasinv = True if "Valorisation" in account.label else False

                yield account

    def get_form_action(self, form_name):
        return self.get_form(id=form_name).url

    def get_profile_name(self):
        return Regexp(CleanText('//div[@id="bloc_identite"]/h5'), r'Bonjour (.*)')(self.doc)


class IbanPage(PDFPage):
    def get_iban(self):
        iban = ''

        # according to re module doc, list returned by re.findall always
        # be in the same order as they are in the source text
        for part in re.findall(r'([A-Z0-9]{4})\1\1', extract_text(self.data), flags=re.MULTILINE):
            # findall will find something like
            # ['FRXX', '1234', ... , '9012', 'FRXX', '1234', ... , '9012']
            iban += part
        iban = iban[:len(iban) // 2]

        # we suppose that all iban are French iban
        iban_last_part = re.findall(r'([A-Z0-9]{3})\1\1Titulaire', extract_text(self.data), flags=re.MULTILINE)
        assert len(iban_last_part) == 1, 'There should have something like 123123123Titulaire'

        iban += iban_last_part[0]
        if is_iban_valid(iban):
            return iban
        self.logger.warning('IBAN %s is not valid', iban)
        return NotAvailable


class BankTransaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^RET(RAIT) DAB (?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*)'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^(CARTE|CB ETRANGER|CB) (?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*)'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^(?P<category>VIR(EMEN)?T? (SEPA)?(RECU|FAVEUR)?)( /FRM)?(?P<text>.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^PRLV (?P<text>.*)( \d+)?$'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^(CHQ|CHEQUE) .*$'), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(AGIOS /|FRAIS) (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(CONVENTION \d+ |F )?COTIS(ATION)? (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(F|R)-(?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^REMISE (?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>.*)( \d+)? QUITTANCE .*'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^.* LE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2})$'), FrenchTransaction.TYPE_UNKNOWN),
        (re.compile(r'^ACHATS (CARTE|CB)'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^ANNUL (?P<text>.*)'), FrenchTransaction.TYPE_PAYBACK)
    ]


class TransactionsPage(LoggedPage, MyHTMLPage):
    COL_DATE = 0
    COL_TEXT = 1
    COL_DEBIT = 2
    COL_CREDIT = 3

    def check_error(self):
        error = CleanText(default="").filter(self.doc.xpath('//p[@class="question"]'))
        return error if u"a expiré" in error else None

    def get_loan_balance(self):
        # Loan balances are positive on the website so we change the sign
        return CleanDecimal.US('//*[@id="table-detail"]/tbody/tr/td[@class="capital"]', sign='-', default=NotAvailable)(self.doc)

    def get_loan_currency(self):
        return Currency('//*[@id="table-detail"]/tbody/tr/td[@class="capital"]', default=NotAvailable)(self.doc)

    def get_loan_ownership(self):
        co_owner = CleanText('//td[@class="coEmprunteur"]')(self.doc)
        if co_owner:
            return AccountOwnership.CO_OWNER
        return AccountOwnership.OWNER

    def open_market(self):
        # only for netfinca PEA
        self.browser.bourse.go()

    def go_action(self, action):
        names = {'investment': "Portefeuille", 'history': "Mouvements"}
        for li in self.doc.xpath('//div[@class="onglets"]/ul/li[not(script)]'):
            if not Attr('.', 'class', default=None)(li) and names[action] in CleanText('.')(li):
                url = Attr('./ancestor::form[1]', 'action')(li)
                args = self.js2args(Attr('./a', 'onclick')(li))
                args['javax.faces.ViewState'] = self.get_view_state()
                self.browser.location(url, data=args)
                break

    @method
    class iter_investment(TableElement):
        item_xpath = '//table[contains(@id, "titres") or contains(@id, "OPCVM")]/tbody/tr'
        head_xpath = '//table[contains(@id, "titres") or contains(@id, "OPCVM")]/thead/tr/th[not(caption)]'

        col_label = 'Intitulé'
        col_quantity = 'NB'
        col_unitprice = re.compile('Prix de revient')
        col_unitvalue = 'Dernier cours'
        col_diff = re.compile('\+/- Values latentes')
        col_valuation = re.compile('Montant')

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_quantity = CleanDecimal(TableCell('quantity'))
            obj_unitprice = CleanDecimal(TableCell('unitprice'))
            obj_unitvalue = CleanDecimal(TableCell('unitvalue'))
            obj_valuation = CleanDecimal(TableCell('valuation'))
            obj_diff = CleanDecimal(TableCell('diff'))

            def obj_code(self):
                onclick = Attr(None, 'onclick').filter((TableCell('label')(self)[0]).xpath('.//a'))
                m = re.search(',\s+\'([^\'_]+)', onclick)
                return NotAvailable if not m else m.group(1)

            def condition(self):
                return CleanText(TableCell('valuation'))(self)

    def more_history(self):
        link = None
        for a in self.doc.xpath('.//a'):
            if a.text is not None and a.text.strip() == 'Sur les 6 derniers mois':
                link = a
                break

        form = self.doc.xpath('//form')[-1]
        if not form.attrib['action']:
            return None

        if link is None:
            # this is a check account
            args = {
                'categorieMouvementSelectionnePagination': 'afficherTout',
                'nbLigneParPageSelectionneHautPagination': -1,
                'nbLigneParPageSelectionneBasPagination': -1,
                'nbLigneParPageSelectionneComponent': -1,
                'idDetail:btnRechercherParNbLigneParPage': '',
                'idDetail_SUBMIT': 1,
                'javax.faces.ViewState': self.get_view_state(),
            }
        else:
            # something like a PEA or so
            value = link.attrib['id']
            id = value.split(':')[0]
            args = {
                '%s:_idcl' % id: value,
                '%s:_link_hidden_' % id: '',
                '%s_SUBMIT' % id: 1,
                'javax.faces.ViewState': self.get_view_state(),
                'paramNumCompte': '',
            }

        self.browser.location(form.attrib['action'], data=args)
        return True

    def get_deferred_card_history(self):
        # get all transactions
        form = self.get_form(id="hiddenCB")
        form['periodeMouvementSelectionnePagination'] = 4
        form['nbLigneParPageSelectionneHautPagination'] = -1
        form['nbLigneParPageSelectionneBasPagination'] = -1
        form['periodeMouvementSelectionneComponent'] = 4
        form['categorieMouvementSelectionneComponent'] = ''
        form['nbLigneParPageSelectionneComponent'] = -1
        form['idDetail:btnRechercherParNbLigneParPage'] = ''
        form['idDetail:btnRechercherParPeriode'] = ''
        form['idDetail_SUBMIT'] = 1
        form['idDetail:_idcl'] = ''
        form['paramNumCompte'] = ''
        form['idDetail:_link_hidden_'] = ''
        form['javax.faces.ViewState'] = self.get_view_state()
        form.submit()

        return True

    def get_history(self):
        # DAT account can't have transaction
        if self.doc.xpath('//table[@id="table-dat"]'):
            return
        # These accounts have investments, no transactions
        if self.doc.xpath('//table[@id="InfosPortefeuille"]'):
            return
        tables = self.doc.xpath('//table[@id="table-detail-operation"]')
        if len(tables) == 0:
            tables = self.doc.xpath('//table[@id="table-detail"]')
        if len(tables) == 0:
            tables = self.doc.xpath('//table[has-class("table-detail")]')
        if len(tables) == 0:
            assert len(self.doc.xpath('//td[has-class("no-result")]')) > 0
            return

        for tr in tables[0].xpath('.//tr'):
            tds = tr.findall('td')
            if len(tds) < 4:
                continue

            t = BankTransaction()
            date = ''.join([txt.strip() for txt in tds[self.COL_DATE].itertext()])
            raw = ''.join([txt.strip() for txt in tds[self.COL_TEXT].itertext()])
            debit = self.parse_number(''.join([txt.strip() for txt in tds[self.COL_DEBIT].itertext()]))
            credit = self.parse_number(''.join([txt.strip() for txt in tds[self.COL_CREDIT].itertext()]))

            t.parse(date, re.sub(r'[ ]+', ' ', raw), vdate=date)
            t.set_amount(credit, debit)
            yield t


class CBTransactionsPage(TransactionsPage):
    COL_CB_CREDIT = 2

    def get_summary(self):
        tables = self.doc.xpath('//table[@id="idDetail:dataCumulAchat"]')
        transactions = list()

        if len(tables) == 0:
            return transactions
        for tr in tables[0].xpath('.//tr'):
            tds = tr.findall('td')
            if len(tds) < 3:
                continue

            t = BankTransaction()
            date = ''.join([txt.strip() for txt in tds[self.COL_DATE].itertext()])
            raw = self.parse_number(''.join([txt.strip() for txt in tds[self.COL_TEXT].itertext()]))
            credit = self.parse_number(''.join([txt.strip() for txt in tds[self.COL_CB_CREDIT].itertext()]))
            debit = ""

            t.parse(date, re.sub(r'[ ]+', ' ', raw))
            t.set_amount(credit, debit)
            transactions.append(t)
        return transactions

    def get_history(self):
        transactions = self.get_summary()
        for histo in super(CBTransactionsPage, self).get_history():
            transactions.append(histo)

        transactions.sort(key=lambda transaction: transaction.date, reverse=True)
        return iter(transactions)


class LifeInsuranceIframe(LoggedPage, HTMLPage):
    def go_to_history(self):
        form = self.get_form(id='aspnetForm')

        form['__EVENTTARGET'] = 'ctl00$Menu$rlbHistorique'

        form.submit()

    def get_transaction_investments_popup(self, mouvement):
        form = self.get_form(id='aspnetForm')

        form['ctl00$ScriptManager1'] = 'ctl00$ContentPlaceHolderMain$upaListMvt|%s' % mouvement
        form['__EVENTTARGET'] = mouvement

        return form.submit()

    @method
    class iter_investment(TableElement):
        item_xpath = '//table[contains(@id,"dgListSupports")]//tr[@class="AltItem" or @class="Item"]'
        head_xpath = '//table[contains(@id,"dgListSupports")]//tr[@class="Header"]/td'

        col_label = re.compile('Supports')
        col_quantity = "Nbre d'UC"
        col_unitprice = re.compile('PMPA')
        col_unitvalue = re.compile('Valeur')
        col_diff = re.compile('Evolution')
        col_valuation = re.compile('Montant')
        col_code = 'Code ISIN'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_quantity = MyDecimal(TableCell('quantity'))
            obj_unitprice = MyDecimal(TableCell('unitprice'))
            obj_unitvalue = MyDecimal(TableCell('unitvalue'))
            obj_valuation = MyDecimal(TableCell('valuation'))
            obj_code = Regexp(CleanText(TableCell('code')), r'(.{12})', default=NotAvailable)
            obj_code_type = lambda self: Investment.CODE_TYPE_ISIN if Field('code')(self) is not NotAvailable else NotAvailable

            def obj_diff_ratio(self):
                diff_percent = MyDecimal(TableCell('diff')(self)[0])(self)
                return diff_percent / 100 if diff_percent != NotAvailable else diff_percent

    @method
    class iter_history(TableElement):
        item_xpath = '//table[@id="ctl00_ContentPlaceHolderMain_PaymentListing_gvInfos"]/tr[not(contains(@class, "Header"))]'
        head_xpath = '//table[@id="ctl00_ContentPlaceHolderMain_PaymentListing_gvInfos"]/tr[@class="Header"]/th'

        col_date = 'Date'
        col_label = re.compile('^Nature')
        col_amount = re.compile('^Montant net de frais')

        class item(ItemElement):
            klass = BankTransaction

            obj_raw = BankTransaction.Raw(CleanText(TableCell('label')))
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)
            obj_amount = MyDecimal(TableCell('amount'))

            def obj_investments(self):
                investments_popup = self.page.get_transaction_investments_popup(Regexp(Link('.//a'), r"\'(.*?)\'")(self))

                # iter from investments_popup to get transaction investments
                return [inv for inv in investments_popup.page.iter_transaction_investments(investments=Env('investments')(self))]

    @method
    class iter_transaction_investments(TableElement):
        item_xpath = '//table[@id="ctl00_ContentPlaceHolderPopin_UcDetailMouvement_UcInvestissement_gvInfos"]/tr[not(contains(@class, "Header"))]'
        head_xpath = '//table[@id="ctl00_ContentPlaceHolderPopin_UcDetailMouvement_UcInvestissement_gvInfos"]/tr[@class="Header"]/th'

        col_label = 'Support'
        col_vdate = 'Date de valeur'
        col_unitprice = "Valeur de l'UC"
        col_quantity = "Nombre d'UC"
        col_valuation = 'Montant'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True)
            obj_quantity = MyDecimal(TableCell('quantity'))
            obj_unitprice = MyDecimal(TableCell('unitprice'))
            obj_valuation = MyDecimal(TableCell('valuation'))
            obj_code_type = lambda self: Investment.CODE_TYPE_ISIN if Field('code')(self) is not NotAvailable else NotAvailable

            def obj_code(self):
                for inv in Env('investments')(self):
                    if inv.label == Field('label')(self):
                        return inv.code

                return NotAvailable


class BoursePage(AbstractPage):
    PARENT = 'lcl'
    PARENT_URL = 'bourse'

    def open_market_next(self):
        # only for netfinca PEA
        # can't do it in separate page/on_load because there might be history on this page...
        self.get_form(id='formToSubmit').submit()

    def go_history_filter(self, cash_filter='all'):
        cash_filter_value = {
            'market': 0,
            'liquidity': 1,
            'all': 'ALL',
        }

        form = self.get_form(id="historyFilter")
        form['cashFilter'] = cash_filter_value[cash_filter]
        # We can't go above 2 years
        form['beginDayfilter'] = (datetime.strptime(form['endDayfilter'], '%d/%m/%Y') - timedelta(days=730)).strftime('%d/%m/%Y')
        form.submit()


class BankProfilePage(LoggedPage, HTMLPage):
    @method
    class get_profile(ItemElement):
        klass = Person

        obj_email = CleanText('//form[@id="idCoordonneePersonnelle"]//table//strong[contains(text(), "e-mail")]/parent::td', children=False)

        obj_phone = CleanText('//form[@id="idCoordonneePersonnelle"]//table//strong[contains(text(), "mobile")]/parent::td', children=False)

        obj_address = Regexp(
            CleanText('//form[@id="idCoordonneePersonnelle"]//table//strong[contains(text(), "adresse fiscale")]/parent::td', children=False),
            r'^(.*?)\/',
        )

    def renew_personal_information(self):
        return CleanText('//p[contains(text(), "Vos données personnelles nous sont nécessaires")]')(self.doc)
