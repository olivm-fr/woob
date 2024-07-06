# Copyright(C) 2019      Budget Insight
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
from decimal import Decimal

from woob.browser.pages import HTMLPage, LoggedPage
from woob.browser.elements import ListElement, ItemElement, method, TableElement
from woob.browser.filters.standard import (
    CleanText, CleanDecimal, Date, Regexp, Field, Currency,
    MapIn, Eval, Title, Env,
)
from woob.browser.filters.html import Link, TableCell, Attr
from woob.capabilities.base import NotAvailable, empty
from woob.capabilities.bank import Account
from woob.capabilities.bank.wealth import Investment, Pocket
from woob.tools.capabilities.bank.transactions import FrenchTransaction


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^(?P<text>.*[Vv]ersement.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>([Aa]rbitrage|[Pp]rélèvements.*))'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^(?P<text>([Rr]etrait|[Pp]aiement.*))'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^(?P<text>.*)'), FrenchTransaction.TYPE_BANK),
    ]


class LoginPage(HTMLPage):
    ENCODING = 'utf-8'

    def login(self, login, password, recaptcha_reponse):
        form = self.get_form(name='bloc_ident')
        form['_cm_user'] = login
        form['_cm_pwd'] = password
        form['g-recaptcha-response'] = recaptcha_reponse
        form.submit(allow_redirects=False)

    def get_captcha_site_key(self):
        return Attr('//div[@class="g-recaptcha"]', 'data-sitekey', default='')(self.doc)


class ActionNeededPage(HTMLPage, LoggedPage):
    def get_message(self):
        return CleanText('//p[@role="heading"]')(self.doc)

    def get_skip_url(self):
        return Link('//a[contains(., "PASSER CETTE ETAPE")]', default=None)(self.doc)


ACCOUNT_TYPES = {
    "pargne entreprise": Account.TYPE_PEE,
    "pargne groupe": Account.TYPE_PEE,
    "pargne retraite": Account.TYPE_PERCO,
    "courant bloqué": Account.TYPE_RSP,
}


class AccountsPage(LoggedPage, HTMLPage):
    @method
    class iter_accounts(ListElement):
        item_xpath = '//th[text()= "Nom du support" or text()="Nom du profil" or text()="Nom du compte"]/ancestor::table/ancestor::table'

        class item(ItemElement):
            klass = Account
            balance_xpath = './/span[contains(text(), "Montant total")]/following-sibling::span'

            obj_label = CleanText('./tbody/tr/th[1]//div')
            obj_balance = CleanDecimal.French(balance_xpath)
            obj_currency = Currency(balance_xpath)
            obj_type = MapIn(Field('label'), ACCOUNT_TYPES, Account.TYPE_UNKNOWN)
            obj_company_name = CleanText('(//p[contains(@class, "profil_entrep")]/text())[1]')
            obj_number = NotAvailable

            def obj__entreprise_or_epargnants(self):
                if Env('is_entreprise')(self):
                    return 'entreprise'
                return 'epargnants'

            def obj_id(self):
                # Use customer number + label to build account id
                number = Regexp(
                    CleanText('//div[@id="ei_tpl_fullSite"]//div[contains(@class, "ei_tpl_profil_content")]/p'),
                    r'(\w+)$',
                    default="",
                )(self)
                return Field('label')(self) + number

    def iter_invest_rows(self, account):
        """
        Process each invest row, extract elements needed to get
        pocket and valuation diff information.
        There are even PERCO rows where invests are located into a 'repartition' element.
        This 'repartition' element contains all the investments of the account when you mouseover it.
        Returns (row, el_repartition, el_pocket, el_diff)
        """
        for row in self.doc.xpath('//th/div[contains(., "%s")]/ancestor::table//table/tbody/tr' % account.label):
            id_repartition = row.xpath('.//td[1]//span[contains(@id, "rootSpan")]/@id')
            id_pocket = row.xpath('.//td[2]//span[contains(@id, "rootSpan")]/@id')
            id_diff = row.xpath('.//td[3]//span[contains(@id, "rootSpan")]/@id')

            if not any(id_repartition or id_pocket or id_diff):
                continue

            yield (
                row,
                self.doc.xpath('//div[contains(@id, "dv::s::%s")]' % id_repartition[0].rsplit(':', 1)[0])[0] if id_repartition else None,
                row.xpath('//div[contains(@id, "dv::s::%s")]' % id_diff[0].rsplit(':', 1)[0])[0] if id_diff else None,
            )

    def get_investment_form(self, form_param):
        return self.get_form(
            id='I0:P5:F',
            submit='.//input[@name = "%s"]' % form_param
        )

    def iter_investments(self, account):
        for row, elem_repartition, elem_diff in self.iter_invest_rows(account=account):
            # If elements can be found in elem_repartition,
            # all the investments can be retrieved in it.
            if elem_repartition is not None:
                for elem in elem_repartition.xpath('.//table//tr[position() > 2]'):
                    inv = Investment()
                    inv._account = account
                    inv.label = CleanText('.//td[1]//a')(elem)
                    inv._form_param = NotAvailable
                    inv._details_url = Link('.//td[1]//a', default=NotAvailable)(elem)
                    inv.valuation = CleanDecimal.French('.//td[2]', default=NotAvailable)(elem)

                    yield inv
            else:
                inv = Investment()
                inv._account = account
                inv.label = CleanText('.//td[1]')(row)
                inv._form_param = CleanText('.//td[1]/input/@name')(row)
                inv._details_url = Link('.//td[1]//a', default=NotAvailable)(row)
                inv.valuation = CleanDecimal.French('.//td[2]')(row)

                if account._entreprise_or_epargnants == 'entreprise':
                    inv.quantity = CleanDecimal.French('.//td[3]', default=NotAvailable)(row)
                    inv.unitvalue = CleanDecimal.French('.//td[4]', default=NotAvailable)(row)
                    portfolio_share = CleanDecimal.French('.//td[5]', default=NotAvailable)(row)
                    if not empty(portfolio_share):
                        inv.portfolio_share = portfolio_share / 100

                # On all Cmes children the row shows percentages and the popup shows absolute values in currency.
                # On Cmes it is mirrored, the popup contains the percentage.
                is_mirrored = '%' in row.text_content()

                if not is_mirrored:
                    inv.diff = CleanDecimal.French('.//td[3]', default=NotAvailable)(row)
                    if elem_diff is not None:
                        inv.diff_ratio = Eval(
                            lambda x: x / 100,
                            CleanDecimal.French(Regexp(CleanText('.'), r'([+-]?[\d\s]+[\d,]+)\s*%'))
                        )(elem_diff)
                else:
                    if elem_diff is not None:
                        inv.diff = CleanDecimal.French('.', default=NotAvailable)(elem_diff)
                        inv.diff_ratio = Eval(
                            lambda x: x / 100,
                            CleanDecimal.French(Regexp(CleanText('.//td[3]'), r'([+-]?[\d\s]+[\d,]+)\s*%'))
                        )(row)
                yield inv

    def iter_ccb_pockets(self, account):
        # CCB accounts have a specific table with more columns and specific attributes
        for row in self.doc.xpath('//th/div[contains(., "%s")]/ancestor::table//table/tbody/tr' % account.label):
            pocket = Pocket()
            pocket._account = account
            pocket.investment = None
            pocket.label = CleanText('.//td[1]')(row)
            pocket.amount = CleanDecimal.French('.//td[last()]')(row)
            if 'DISPONIBLE' in CleanText('.//td[2]')(row):
                pocket.condition = Pocket.CONDITION_AVAILABLE
                pocket.availability_date = NotAvailable
            else:
                pocket.condition = Pocket.CONDITION_DATE
                pocket.availability_date = Date(CleanText('.//td[2]'), dayfirst=True)(row)
            yield pocket


class InvestmentPage(LoggedPage, HTMLPage):
    def get_asset_management_url(self):
        return Link('//a[.//span[text()="Fiche valeur"]]', default=None)(self.doc)

    @method
    class fill_investment(ItemElement):
        # Sometimes there is a 'LIBELLES EN EURO' string joined with the category so we remove it
        def obj_asset_category(self):
            asset_category = Title(
                CleanText(
                    '//tr[th[text()="Classification AMF"]]/td',
                    replace=[('LIBELLES EN EURO', '')]
                )
            )(self)
            if asset_category == 'Sans Classification':
                return NotAvailable
            return asset_category

        def obj_srri(self):
            # Extract the value from '1/7' or '6/7' for instance
            srri = Regexp(CleanText('//tr[th[text()="Niveau de risque"]]/td'), r'(\d+)\s?/7', default=None)(self)
            if srri:
                return int(srri)
            return NotAvailable

        def obj_recommended_period(self):
            period = CleanText('//tr[th[text()="Durée de placement recommandée"]]/td')(self)
            if period != 'NC':
                return period
            return NotAvailable

    def get_form_url(self):
        form = self.get_form(id='C:P:F')
        return form.url

    def get_performance(self):
        return Eval(lambda x: x / 100, CleanDecimal.French('//p[contains(@class, "plusvalue--value")]'))(self.doc)

    def get_investment_details(self):
        return Link('//a[text()="Mes avoirs" or text()="Mon épargne"]', default=NotAvailable)(self.doc)

    def go_back(self):
        go_back_url = Link('//a[@id="C:A"]')(self.doc)
        self.browser.location(go_back_url)


POCKET_CONDITIONS = {
    'retraite': Pocket.CONDITION_RETIREMENT,
    'disponibilites': Pocket.CONDITION_DATE,
    'immediate': Pocket.CONDITION_AVAILABLE,
}


class InvestmentDetailsPage(LoggedPage, HTMLPage):
    @method
    class fill_investment(ItemElement):
        def obj_quantity(self):
            # Total quantity of the investments accross all contracts (PEE & PER).
            total_quantity = CleanDecimal.French('//tr[th[text()="Nombre de parts"]]//em')(self)
            # Quantity of the investments for PER contracts.
            per_quantity = CleanDecimal.French(
                '//tr[contains(td, "A votre retraite")]/td[3]',
                default=NotAvailable,
            )(self)

            if Env('account_type')(self) != Account.TYPE_PERCO:
                if not empty(per_quantity):
                    # If there is a per_quantity, we need to substract it to total_quantity.
                    return total_quantity - per_quantity
                return total_quantity

            else:
                # If there is a PER split in 2 parts (Libre & Piloté), we cannot fetch the quantity
                # displayed on the InvestmentDetailsPage because it's an aggregate of both plans.
                # To avoid any data discrepancy, we must compute quantity for PERs.
                if not empty(self.obj.valuation) and not empty(self.obj.unitvalue):
                    return Decimal.quantize(
                        Decimal(self.obj.valuation / self.obj.unitvalue),
                        Decimal('0.0001'),
                    )
                return NotAvailable

        obj_unitvalue = CleanDecimal.French(
            '//tr[th[contains(text(), "Valeur de la part")]]//em',
            default=NotAvailable
        )

        obj_vdate = Date(
            Regexp(
                CleanText('//tr//th[contains(text(), "Valeur de la part")]'),
                r'Valeur de la part au (.*)',
                default=NotAvailable
            ),
            default=NotAvailable,
            dayfirst=True
        )

    def go_back(self):
        go_back_url = Link('//a[@id="C:A"]')(self.doc)
        self.browser.location(go_back_url)

    @method
    class iter_pockets(TableElement):
        item_xpath = '//table[contains(caption/span/text(), "Détail par échéance")]/tbody/tr'
        head_xpath = '//table[contains(caption/span/text(), "Détail par échéance")]/thead//th'

        col_condition = 'Echéance'
        col_amount = 'Montant investi'
        col_quantity = 'Nombre de parts'

        class item(ItemElement):
            klass = Pocket

            def condition(self):
                if Field('condition')(self) == Pocket.CONDITION_RETIREMENT:
                    if Env('acc')(self).type == Account.TYPE_PERCO:
                        return True
                else:
                    if Env('acc')(self).type == Account.TYPE_PEE:
                        return True

                return False

            obj_investment = Env('inv')
            obj_amount = CleanDecimal.French(TableCell('amount'))
            obj_quantity = CleanDecimal.French(TableCell('quantity'), default=NotAvailable)

            def obj_label(self):
                return Env('inv')(self).label

            def obj_condition(self):
                condition_text = CleanText(TableCell('condition'), transliterate=True)(self)
                condition = MapIn(self, POCKET_CONDITIONS, Pocket.CONDITION_UNKNOWN).filter(condition_text.lower())
                if condition == Pocket.CONDITION_UNKNOWN:
                    self.page.logger.warning('Unhandled availability condition for pockets: %s', condition_text)
                return condition

            def obj_availability_date(self):
                if Field('condition')(self) == Pocket.CONDITION_DATE:
                    return Date(
                        Regexp(CleanText(TableCell('condition')), r'Disponibilités (.*)'),
                        dayfirst=True,
                    )(self)


class OperationPage(LoggedPage, HTMLPage):
    # Most '_account_label' correspond 'account.label', but there are exceptions
    ACCOUNTS_SPE_LABELS = {
        'CCB': 'Compte courant bloqué',
    }

    @method
    class get_transactions(ListElement):
        item_xpath = '//tr[@id]'

        class item(ItemElement):
            klass = Transaction

            obj_amount = CleanDecimal.French('./th[@scope="rowgroup"][2]')
            obj_label = CleanText('(//p[contains(@id, "smltitle")])[2]')
            obj_raw = Transaction.Raw(Field('label'))
            obj_date = Date(
                Regexp(
                    CleanText('(//p[contains(@id, "smltitle")])[1]'),
                    r'(\d{1,2}/\d{1,2}/\d+)'
                ),
                dayfirst=True
            )

            def obj__account_label(self):
                account_label = CleanText('./th[@scope="rowgroup"][1]')(self)
                return self.page.ACCOUNTS_SPE_LABELS.get(account_label, account_label)

    @method
    class get_entreprise_transactions(TableElement):
        item_xpath = '//table[contains(@class, "repartition")]//tbody//tr[position() > 1]'
        head_xpath = '//table[contains(@class, "repartition")]//thead//th'

        col_label = 'Support'
        col_amount = 'Montant versé'

        def store(self, obj):
            # This code enables indexing transaction_id when there
            # are several transactions with the exact same id.
            tr_id = obj.id
            n = 1
            while tr_id in self.objects:
                tr_id = '%s-%s' % (obj.id, n)
                n += 1
            obj.id = tr_id
            self.objects[obj.id] = obj
            return obj

        class item(ItemElement):
            klass = Transaction

            obj_id = Regexp(
                CleanText('//span[contains(text(), "Détail du")]'),
                r'n° (\d+)'
            )
            obj_amount = CleanDecimal.French(TableCell('amount'))
            obj_label = CleanText(TableCell('label'))
            obj_raw = Transaction.Raw(Field('label'))
            obj_date = Date(
                CleanText('(//th[text()="Date d\'effet"]//following::td)[1]'),
                dayfirst=True
            )


class OperationsListPage(LoggedPage, HTMLPage):
    def __init__(self, *a, **kw):
        self._cache = []
        super(OperationsListPage, self).__init__(*a, **kw)

    def get_operations_idx(self, entreprise_or_epargnants):
        if entreprise_or_epargnants == 'entreprise':
            return [i.split('=')[-1] for i in self.doc.xpath('//a[contains(@href, "GoOperationDetail")]/@href')]
        return [i.split(':')[-1] for i in self.doc.xpath('.//input[contains(@name, "_FID_GoOperationDetails")]/@name')]
