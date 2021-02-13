# -*- coding: utf-8 -*-

# Copyright(C) 2012-2017 Jean Walrave
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

from weboob.browser.pages import HTMLPage, PDFPage, LoggedPage
from weboob.browser.elements import TableElement, ListElement, ItemElement, method
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Regexp, Field, Date, Coalesce,
    Map, Currency,
)
from weboob.browser.filters.html import Attr, TableCell, ReplaceEntities
from weboob.capabilities.base import NotAvailable
from weboob.capabilities.bank import Account, Loan
from weboob.capabilities.wealth import Investment
from weboob.tools.capabilities.bank.investments import IsinCode, IsinType
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.capabilities.bank.iban import is_iban_valid
from weboob.exceptions import ActionNeeded, BrowserUnavailable


class SecretTooShort(Exception):
    # secret is a word which contains at least 8 char and website ask us to enter 2 chars of it
    # char 3 and 4 or 6 and 7
    # but sometimes it ask us to enter char 9 and 10 even if secret code contains just 8 char
    # maybe because user didn't enter full secret code or because website is badly coded (who knows...)
    pass


class StatefulPage(LoggedPage, HTMLPage):
    def get_form_for_menu(self, menu):
        btn = Regexp(
            Attr('//div[@class="menuvert"]//a[contains(., "%s")]' % menu, 'onclick'),
            r"\('', '(.*?)',"
        )(self.doc)
        form = self.get_form(id='form1')
        form['MODE'] = 'NAVMENU_%s' % btn
        return form

    def go_to_menu(self, menu):
        form = self.get_form_for_menu(menu)
        form.submit()

    def go_to_account(self, account):
        token = self.isolate_token()

        form = self.get_form(id='form1')

        for attr in list(form):
            if attr not in (
                'MENUSTATE',
                'DEVICE_SIZE_INFO',
                'C11__GETMODULENOTEPAD[1].IOGETMODULENOTEPAD[1].OUTPUTPARAMETER[1].TEXT',
                token[0],
            ):
                del form[attr]

        form['MODE'] = account._btn
        form['C4__AUTOSELECTOR_TBL_4F9C27E5D4E67F554398'] = account._btn[-1]
        form['C4__AUTOSELECTOR_TBL_DASHBOARD'] = account._btn[-3]

        form.submit()

    def isolate_token(self):
        return (
            Attr('(//input[@type="hidden"])[2]', 'name')(self.doc),
            Attr('(//input[@type="hidden"])[2]', 'value')(self.doc),
        )


class LoginPage(HTMLPage):
    def is_here(self):
        return bool(CleanText('//div[@class="zone-authent"]')(self.doc))

    def on_load(self):
        message = CleanText("//div[contains(@class, 'bloc-message error')]//h3")(self.doc)
        text = "Suite à une erreur technique, cette page ne peut pas s'afficher actuellement."
        if text in message:
            # For now, the message is too related to the bank website, we don't return it.
            raise BrowserUnavailable()

    def login(self, login, passwd):
        form = self.get_form(id='form1')

        form['MODE'] = 'C1____09AE2D522D145CD3 FormButton 27'
        form['C1__IOPARAMETERWS[1].INPUTPARAMETER[1].LOGINID'] = login
        form['C1__IOPARAMETERWS[1].INPUTPARAMETER[1].PASSWORD'] = passwd

        form.submit()

    def login_secret(self, secret):
        label = CleanText('//label[@for="C1__IdTwoLetters"]')(self.doc).strip()

        letters = ''
        for n in re.findall(r'(\d+)', label):
            if int(n) > len(secret):
                raise SecretTooShort()
            letters += secret[int(n) - 1]

        if ' ' in letters:
            return False

        form = self.get_form(id='form1')
        form['MODE'] = 'C1____09AE2D522D145CD3 FormButton 29'
        form['C1__IOPARAMETERWS[1].INPUTPARAMETER[1].LETTERSSECSUP'] = letters
        form.submit()
        return True

    def get_error_message(self):
        return CleanText('//div[@class="bloc-message error" and not(@style)]')(self.doc)


ACCOUNT_TYPES = {
    'Liquidités': Account.TYPE_CHECKING,
    'Epargne': Account.TYPE_SAVINGS,
    'Titres': Account.TYPE_MARKET,
    'Engagement/Crédits': Account.TYPE_LOAN,
}

ACCOUNT_EXTRA_TYPES = {
    'BMOOVIE': Account.TYPE_LIFE_INSURANCE,
    'B. GESTION VIE': Account.TYPE_LIFE_INSURANCE,
    'E VIE MILLEIS': Account.TYPE_LIFE_INSURANCE,
    'BANQUE PRIVILEGE': Account.TYPE_REVOLVING_CREDIT,
    'PRET PERSONNEL': Account.TYPE_LOAN,
    'CREDIT IMMOBILIE': Account.TYPE_LOAN,
}

ACCOUNT_TYPE_TO_STR = {
    Account.TYPE_MARKET: 'TTR',
    Account.TYPE_CARD: 'CRT',
}


class AccountsPage(StatefulPage):
    def is_here(self):
        return bool(self.doc.xpath('//h1[contains(., "Mes comptes")]'))

    @method
    class iter_accounts(ListElement):
        item_xpath = u'//tr[contains(@id, "C4__p0_TBL_DASHBOARD")]'

        class item(ItemElement):
            klass = Account

            obj_label = CleanText('.//td[1]//span')
            obj__uncleaned_id = CleanText('.//td[2]//a')
            obj__btn = Attr('.//button', 'name', default=None)
            obj__attached_account = NotAvailable  # for card account only

            def obj_id(self):
                return '%s%s' % (
                    re.sub(r'\s', '', str(Field('_uncleaned_id')(self))),
                    ACCOUNT_TYPE_TO_STR.get(Field('type')(self), ''),
                )

            def is_card(self):
                return bool(self.xpath(
                    './/div[contains(@id, "9385968FC88E7527131931") and not(contains(@style, "display: none;"))]'
                ))

            def obj_balance(self):
                if self.is_card():
                    return 0
                return CleanDecimal.French('.//td[4]//div[1]/a', default=NotAvailable)(self)

            def obj_coming(self):
                if self.is_card():
                    return CleanDecimal.French('.//td[4]//div[1]/a', default=NotAvailable)(self)
                return NotAvailable

            obj_currency = Currency(CleanText('.//td[5]//div[1]/a'))

            def obj_type(self):
                if self.is_card():
                    return Account.TYPE_CARD

                return Coalesce(
                    Map(Field('label'), ACCOUNT_EXTRA_TYPES, ''),
                    Map(
                        CleanText('./ancestor::node()[7]//button[contains(@id, "C4__BUT_787E7BC48BF75E723710")]'),
                        ACCOUNT_TYPES,
                        ''
                    ),
                    default=Account.TYPE_UNKNOWN
                )(self)

            def obj__multiple_type(self):
                # Sometimes an account can be declared twice with different types but the same id.
                # We flag them to avoid some errors
                for account in self.parent.objects.values():
                    if account._uncleaned_id == Field('_uncleaned_id')(self):
                        if not account._multiple_type:
                            account._multiple_type = True
                        return True
                return False

            def obj__fetch_history(self):
                # We only fetch history for the first occurrence of a "multiple-type" account
                return not Field('_multiple_type')(self)


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'\w+ FRAIS RET DAB '), FrenchTransaction.TYPE_BANK),
        (
            re.compile(r'^RET DAB (?P<text>.*?) RETRAIT DU (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}).*'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (re.compile(r'^RET DAB (?P<text>.*?) CARTE ?:.*'), FrenchTransaction.TYPE_WITHDRAWAL),
        (
            re.compile(r'^RET DAB (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2}) (?P<text>.*?) CARTE .*'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'(?P<text>.*) RET DAB DU (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2}) (?P<text2>.*?) CARTE .*'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^(?P<text>.*) RETRAIT DU (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) .*'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'(\w+) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) CB[:*][^ ]+ (?P<text>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'(?P<text>.*) ACHAT DU (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2}) CARTE .*'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<category>VIR(EMEN)?T? (SEPA)?(RECU|FAVEUR)?)( /FRM)?(?P<text>.*)'),
            FrenchTransaction.TYPE_TRANSFER,
        ),
        (re.compile(r'^PRLV (?P<text>.*) (?:REF: \w+ DE (?P<text2>.*))?$'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'PRELEVEMENT (?P<text>.*)'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^CHEQUE.*? (REF \w+)?$'), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(AGIOS /|FRAIS) (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'.*(CONVENTION \d+ )?COTIS(ATION)? (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^REMISE (?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>.*)( \d+)? QUITTANCE .*'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^.* LE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2})$'), FrenchTransaction.TYPE_UNKNOWN),
        (re.compile(r'^CARTE .*'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'CONTRIBUTIONS SOCIALES'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'COMMISSION INTERVENTION'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'INTERETS (CREDITEURS|ANNUELS)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'(ANNUL |ANNULATION |)FRAIS '), FrenchTransaction.TYPE_BANK),
        (re.compile(r'(ANNUL |ANNULATION |)INT DEB'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'TAEG APPLIQUE '), FrenchTransaction.TYPE_BANK),
    ]


class AbstractAccountPage(StatefulPage):
    def has_iban(self):
        return len(self.doc.xpath(
            '//a[contains(., "Edition RIB")]/ancestor::node()[2][not(contains(@style, "display: none;"))]'
        )) > 1

    def has_history(self):
        return bool(self.doc.xpath(
            '//div[contains(@id, "83B48AC016951684534547") and contains(@style, "display: none;")]'
        ))

    def form_to_history_page(self):
        btn = Attr('//button[contains(@id, "moreOperations")]', 'name', default=NotAvailable)(self.doc)

        if btn is NotAvailable:
            return

        token = self.isolate_token()
        form = self.get_form(id='form1')

        for attr in list(form):
            if attr not in (
                'MENUSTATE',
                'DEVICE_SIZE_INFO',
                'C4__WORKING[1].IDENTINTCONTRAT',
                'C9__GETMODULENOTEPAD[1].IOGETMODULENOTEPAD[1].OUTPUTPARAMETER[1].TEXT',
                token[0],
            ):
                del form[attr]

        form['MODE'] = btn

        return form

    @method
    class iter_history(TableElement):
        head_xpath = '//table[@class="table_operations"]/thead/tr/th//a/text()'
        item_xpath = '//table[@class="table_operations"]/tbody/tr'

        col_date = 'Date Opération'
        col_vdate = 'Date valeur'
        col_debit = 'Débit'
        col_credit = 'Crédit'

        class item(ItemElement):
            klass = Transaction

            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True)
            obj_amount = Coalesce(
                CleanDecimal.French(TableCell('credit'), default=None),
                CleanDecimal.French(TableCell('debit')),
            )
            obj_raw = Transaction.Raw(ReplaceEntities(Regexp(
                CleanText('.//script[1]'),
                r"toggleDetails\([^,]+,[^,]+, '(.*?)', '(.*?)', '(.*?)',",
                r'\1 \2 \3'
            )))


class AccountPage(AbstractAccountPage):
    def is_here(self):
        return bool(CleanText('//select[@name="C4__WORKING[1].IDENTINTCONTRAT"]')(self.doc))


class MarketAccountPage(AbstractAccountPage):
    def is_here(self):
        return bool(self.doc.xpath('//select[@name="C4__WORKING[1].SELECTEDSECURITYACCOUNTID"]'))

    def get_space_attrs(self, space):
        a = self.doc.xpath('//a[contains(span, $space)]', space=space)
        if not a:
            self.logger.debug('there is no "mouvements" link on this page')
            return None

        a = Regexp(Attr('.', 'onclick'), r'\((.*?)\)')(a[0]).replace('\'', '').split(', ')
        form = self.get_form(id='form1')

        return (
            a[1],
            'C4__WORKING[1].SELECTEDSECURITYACCOUNTID',
            form['C4__WORKING[1].SELECTEDSECURITYACCOUNTID'],
            a[2],
        )

    @method
    class iter_investments(TableElement):
        head_xpath = '//table[@class="table-titres"]/thead/tr/th'
        item_xpath = '//table[@class="table-titres"]/tbody/tr'

        col_label = 'Valeur'
        col_quantity = 'qté'
        col_unitvalue = 'Cours'
        col_unitprice = 'PAM'
        col_valuation = 'Valorisation'
        col_portfolio_share = '%'
        col_code = 'Code'

        class item(ItemElement):
            klass = Investment

            def condition(self):
                return CleanDecimal.French(TableCell('valuation'), default=None)(self)

            def obj_label(self):
                if not CleanText(TableCell('code'))(self):
                    return CleanText('./preceding-sibling::tr[1]/td[2]')(self)
                return CleanText(TableCell('label'))(self)

            def obj_code(self):
                if CleanText(TableCell('code'))(self):
                    return IsinCode(CleanText(TableCell('code')), default=NotAvailable)(self)
                return IsinCode(CleanText('./preceding-sibling::tr[1]/td[1]'), default=NotAvailable)(self)

            obj_quantity = CleanDecimal.French(TableCell('quantity'), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'), default=NotAvailable)
            obj_valuation = CleanDecimal.French(TableCell('valuation'), default=NotAvailable)
            obj_unitprice = CleanDecimal.French(TableCell('unitprice'))
            obj_code_type = IsinType(Field('code'))

            def obj_portfolio_share(self):
                portfolio_share_percent = CleanDecimal.French(TableCell('portfolio_share'), default=None)(self)
                if portfolio_share_percent is not None:
                    return portfolio_share_percent / 100
                return NotAvailable


class LifeInsuranceAccountPage(AbstractAccountPage):
    def is_here(self):
        return bool(CleanText('//select[@name="C4__WORKING[1].IDENTCONTRACTLIST"]')(self.doc))

    def has_history(self):
        return True

    def get_space_attrs(self, space):
        a = self.doc.xpath('//a[contains(span, $space)]', space=space)
        if not a:
            self.logger.debug('there is no "mouvements" link on this page')
            return None

        a = Regexp(Attr('.', 'onclick'), r'\((.*?)\)')(a[0]).replace('\'', '').split(', ')
        form = self.get_form(id='form1')

        return (
            a[1],
            'C4__WORKING[1].IDENTCONTRACTLIST',
            form['C4__WORKING[1].IDENTCONTRACTLIST'],
            a[2],
        )

    @method
    class iter_history(TableElement):
        head_xpath = '//table[@id="C4__TBL_MVT"]/thead/tr/th//a/text()'
        item_xpath = '//table[@id="C4__TBL_MVT"]/tbody/tr'

        col_label = u'Opération'
        col_vdate = u'Date d\'effet'
        col_amount = u'Montant net'

        class item(ItemElement):
            klass = Transaction

            obj_raw = Transaction.Raw(TableCell('label'))
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True)

            def obj_amount(self):
                return CleanDecimal.French('.//div/span', default=NotAvailable)(TableCell('amount')(self)[0])

            def obj_date(self):
                return Date(CleanText('.//span[contains(@id, "C4__QUE_50FADFF19F566198286748")]'), dayfirst=True)(self)

    @method
    class iter_investments(TableElement):
        def condition(self):
            return not self.xpath('//h1[text()="Aucune position"]')

        head_xpath = '//table[@class="table-support"]/thead/tr/th'
        item_xpath = '//table[@class="table-support"]/tbody/tr'

        col_label = 'Supports'
        col_quantity = u'Quantité'
        col_unitvalue = 'Valeur'
        col_valuation = 'Evaluation'
        col_portfolio_share = u'%'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_quantity = CleanDecimal.French(TableCell('quantity'), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'), default=NotAvailable)
            obj_valuation = CleanDecimal.French(TableCell('valuation'))

            def obj_portfolio_share(self):
                portfolio_share_percent = CleanDecimal.French(TableCell('portfolio_share'), default=None)(self)
                if portfolio_share_percent is not None:
                    return portfolio_share_percent / 100
                return NotAvailable

            obj_code = NotAvailable
            obj_code_type = NotAvailable


class CardPage(AbstractAccountPage):
    def is_here(self):
        return bool(CleanText('//span[contains(., "Détail carte")]')(self.doc))

    def is_immediate_card(self):
        # If the label is "Echéance au  :" without a date, it's not a deferred card.
        return bool(CleanText('//div[@class="echeance"]//label[.="Echéance au  :"]')(self.doc))

    def has_iban(self):
        return False

    def do_account_attachment(self, accounts):
        caccount_aid = CleanText('//span[@id="C4__QUE_B160DC66D26AA39615599"]')(self.doc)
        m = re.search('-(.*?)-', caccount_aid)
        if m:
            regex = m.group(1)
            for account in accounts:
                if account.id == re.sub(r'\s', '', regex):
                    return account
        return NotAvailable

    def has_history(self):
        return bool(self.doc.xpath(
            '//h1[contains(@id, "C0B43C670D16A2667437")]/ancestor::node()[2][contains(@style, "display: none;")]'
        ))

    @method
    class iter_history(TableElement):
        head_xpath = '//table[@class="table-cartes"]/thead/tr/th//a'
        item_xpath = '//table[@class="table-cartes"]/tbody/tr'

        col_label = u'Libellé'
        col_date = u'Date opération'

        class item(ItemElement):
            klass = Transaction

            def condition(self):
                tds = self.el.xpath('./td')
                if len(tds) == 1 and 'colspan' in tds[0].attrib:
                    assert self.page.doc.xpath('//h1[text()="Aucune opération"]')
                    return False
                return True

            obj_label = CleanText(TableCell('label'))
            obj_type = Transaction.TYPE_DEFERRED_CARD
            obj_rdate = Date(CleanText(TableCell('date')), dayfirst=True)

            def obj_date(self):
                return self.page.get_debit_date()

            def obj_amount(self):
                return CleanDecimal.French('./td[5]//div/span', default=NotAvailable)(self)

    def get_debit_date(self):
        return Date(
            Regexp(CleanText('//label[starts-with(text(),"Echéance au ")]'), r'(\d{2}/\d{2}/\d{4})'),
            dayfirst=True,
        )(self.doc)

    def get_space_attrs(self, space):
        a = self.doc.xpath('//a[contains(span, $space)]', space=space)
        if not a:
            self.logger.debug('there is no %r link on this page', space)
            return None

        a = Regexp(Attr('.', 'onclick'), r'\((.*?)\)')(a[0]).replace('\'', '').split(', ')
        form = self.get_form(id='form1')

        return (
            a[1],
            'C4__WORKING[1].LISTCONTRATS',
            form['C4__WORKING[1].LISTCONTRATS'],
            a[2],
        )


class RevolvingAccountPage(AbstractAccountPage):
    def is_here(self):
        return bool(CleanText('//span[contains(., "Crédit renouvelable")]')(self.doc))

    def has_iban(self):
        return False

    def get_revolving_attributes(self, account):
        loan = Loan()

        loan.available_amount = CleanDecimal.French(
            '//div/span[contains(text(), "Montant disponible")]/following-sibling::*[1]'
        )(self.doc)
        loan.used_amount = CleanDecimal.French(
            '//div/span[contains(text(), "Montant Utilisé")]/following-sibling::*[1]'
        )(self.doc)
        loan.total_amount = CleanDecimal.French(
            '//div/span[contains(text(), "Réserve accordée")]/following-sibling::*[1]'
        )(self.doc)
        loan.last_payment_amount = CleanDecimal.French(
            '//div/span[contains(text(), "Echéance Précédente")]/following-sibling::*[1]'
        )(self.doc)
        loan.last_payment_date = Date(
            Regexp(
                CleanText('//div/span[contains(text(), "Echéance Précédente")]/following-sibling::*[2]'),
                r'(\d{2}\/\d{2}\/\d{4})'
            ),
            dayfirst=True
        )(self.doc)
        owner_name = CleanText('//a[@class="lien-entete login"]/span')(self.doc)
        loan.name = ' '.join(owner_name.split()[1:])

        loan.id = account.id
        loan.currency = account.currency
        loan.label = account.label
        loan.balance = account.balance
        loan.coming = account.coming
        loan.type = account.type
        loan._uncleaned_id = account._uncleaned_id
        loan._multiple_type = account._multiple_type
        loan._fetch_history = account._fetch_history
        return loan


class LoanAccountPage(AbstractAccountPage):
    def is_here(self):
        return bool(CleanText('//span[contains(., "prêt")]')(self.doc))

    def has_iban(self):
        return False

    def get_loan_attributes(self, account):
        loan = Loan()
        loan.total_amount = CleanDecimal.French(
            '//div/span[contains(text(), "Capital initial")]/following-sibling::*[1]'
        )(self.doc)
        owner_name = CleanText('//a[@class="lien-entete login"]/span')(self.doc)
        loan.name = ' '.join(owner_name.split()[1:])
        loan.subscription_date = Date(
            Regexp(CleanText('//h4[span[contains(text(), "Date de départ du prêt")]]'), r'(\d{2}\/\d{2}\/\d{4})'),
            dayfirst=True,
        )(self.doc)
        loan.maturity_date = Date(
            Regexp(CleanText('//h4[span[contains(text(), "Date de fin du prêt")]]'), r'(\d{2}\/\d{2}\/\d{4})'),
            dayfirst=True,
        )(self.doc)

        loan.rate = CleanDecimal.French('//div/span[contains(text(), "Taux fixe")]/following-sibling::*[1]')(self.doc)
        loan.last_payment_amount = CleanDecimal.French(
            '//div[@class="txt-detail  " and not (@style)]//span[contains(text(), "Echéance du")]/following-sibling::span[1]'
        )(self.doc)
        loan.last_payment_date = Date(
            Regexp(
                CleanText('//div[@class="txt-detail  " and not (@style)]//span[contains(text(), "Echéance du")]'),
                r'(\d{2}\/\d{2}\/\d{4})'
            ),
            dayfirst=True,
        )(self.doc)

        loan.id = account.id
        loan.currency = account.currency
        loan.label = account.label
        loan.balance = account.balance
        loan.coming = account.coming
        loan.type = account.type
        loan._uncleaned_id = account._uncleaned_id
        loan._multiple_type = account._multiple_type
        loan._fetch_history = account._fetch_history
        return loan


class IbanPDFPage(LoggedPage, PDFPage):
    def get_iban(self):
        match = re.search(r'Tm \[\((FR[0-9]{2} [A-Z0-9 ]+)\)\]', self.doc.decode('ISO8859-1'))

        if not match:
            return NotAvailable

        iban = match.group(1).replace(' ', '')

        assert is_iban_valid(iban)

        return iban


class ActionNeededPage(LoggedPage, HTMLPage):
    def on_load(self):
        # We only need 2 sentences because the thirds is too specific
        message = CleanText("//h2[contains(@class, 'ecDIB')]")(self.doc).split('.')
        raise ActionNeeded('%s.' % '.'.join(message[:-2]))

    def is_here(self):
        message = CleanText("//h2[contains(@class, 'ecDIB')]")(self.doc)
        text = "Afin de respecter nos obligations réglementaires, nous devons disposer d’une connaissance récente des données de nos clients."
        return message and text in message
