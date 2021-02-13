# -*- coding: utf-8 -*-

# Copyright(C) 2015 Budget Insight
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

import xlrd
import datetime

from dateutil.relativedelta import relativedelta
from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.keys import Keys


from weboob.capabilities.bank import Account, Transaction
from weboob.capabilities.base import NotAvailable
from weboob.browser.pages import LoggedPage, Page
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Date, Format,
    Field, Currency,
)
from weboob.browser.filters.json import Dict
from weboob.browser.elements import ItemElement, DictElement, method
from weboob.tools.decorators import retry
from weboob.browser.selenium import (
    SeleniumPage, VisibleXPath, AnyCondition, AllCondition,
    StablePageCondition,
)


class LoginPage(SeleniumPage):
    is_here = AllCondition(
        VisibleXPath('//div[span[contains(text(), "Identifiant")]]/following-sibling::input'),
        VisibleXPath('//div[span[contains(text(), "Mot de passe")]]/following-sibling::input'),
        VisibleXPath('//div[span[span[contains(text(), "Valider")]]]'),
    )

    def login(self, username, password):
        el = self.driver.find_element_by_xpath('//div[span[contains(text(), "Identifiant")]]/following-sibling::input')
        el.click()
        el.send_keys(username)

        el = self.driver.find_element_by_xpath('//div[span[contains(text(), "Mot de passe")]]/following-sibling::input')
        el.click()
        el.send_keys(password)

        el.send_keys(Keys.RETURN)

    def get_error(self):
        return (
            CleanText('//h1[contains(@class, "Notification-caption")]')(self.doc) or
            CleanText('//div[@class="popupContent"]//div[contains(text(), "mot de passe a expiré")]')(self.doc)
        )


class AccueilPage(LoggedPage, SeleniumPage):
    is_here = AllCondition(
        VisibleXPath('//div[@id="Menu-responsive"]'),
        VisibleXPath('//div[contains(@class, "page-title")][contains(text(), "Accueil")]'),
    )


class AccountsPage(LoggedPage, SeleniumPage):
    is_here = AllCondition(
        VisibleXPath('//div[contains(@class, "margin-donwload-btn")]'),
        VisibleXPath('//thead[contains(@class, "v-grid-header")]'),
        VisibleXPath('//tbody[contains(@class, "v-grid-body")]'),
    )

    def download_accounts(self):
        el = self.driver.find_element_by_xpath('//div[contains(@class, "link-margin-donwload-btn")]')
        el.click()


class XLSPage(Page):
    HEADER = 1
    SHEET_INDEX = 0

    def __init__(self, browser, file_path, response):
        self.file_path = file_path
        super(XLSPage, self).__init__(browser, response)

    def build_doc(self, content):
        wb = xlrd.open_workbook(self.file_path)
        sh = wb.sheet_by_index(self.SHEET_INDEX)

        header = None
        drows = []
        rows = []
        for i in range(sh.nrows):
            if self.HEADER and i + 1 < self.HEADER:
                continue
            row = sh.row_values(i)
            if header is None and self.HEADER:
                header = [s.replace('/', '') for s in row]
            else:
                rows.append(row)
                if header:
                    drow = {}
                    for i, cell in enumerate(sh.row_values(i)):
                        drow[header[i]] = cell
                    drows.append(drow)
        return drows if header is not None else rows


class AccountsXlsPage(LoggedPage, XLSPage):
    HEADER = 2  # the first row is empty, the second contains headers

    @method
    class iter_accounts(DictElement):
        class item(ItemElement):
            klass = Account

            # 'service_number' alone is not enough because a person with the
            # same service_number might have multiple cards.
            # And a card number can be associated to multiple persons.
            obj_id = obj_number = Format(
                '%s_%s',
                Field('_service_number'),
                Field('_card_number'),
            )

            def obj_label(self):
                card_number = Field('_card_number')(self)
                last_card_digits = card_number[card_number.rfind('X') + 1:]
                return '%s %s %s' % (
                    Dict('nom titulaire')(self),
                    Dict('prénom titulaire')(self),
                    last_card_digits,
                )

            obj_currency = 'EUR'
            obj_type = Account.TYPE_CARD
            obj__card_number = CleanText(Dict('numero carte'))
            obj__service_number = CleanText(Dict('Numéro de prestation'))


class DeferredQuery(Exception):
    pass


class HistoryPage(LoggedPage, SeleniumPage):
    is_here = VisibleXPath('//div[contains(text(), "Recherche opérations")]')

    def go_transactions_list_tab(self):
        el = self.driver.find_element_by_xpath('//div[contains(text(), "Recherche opérations")]')
        el.click()

        self.browser.wait_xpath_clickable('//div[contains(@class, "v-widget")][div[div[@id="BTN_SEARCH"]]]')

    def get_limit_date_index(self):
        # We suppose we already selected an account.
        el = self.driver.find_element_by_xpath('//input[contains(@placeholder, "De la date d\'arrêté")]')
        el.click()

        # Read dates from dropdown menu, choose 1 year ago max
        self.browser.wait_xpath_visible('//div[@id="VAADIN_COMBOBOX_OPTIONLIST"]')
        dates_text = CleanText('//div[@id="VAADIN_COMBOBOX_OPTIONLIST"]//div[contains(@class, "suggestmenu")]')(self.doc)
        dates_list = dates_text.split()
        today = datetime.date.today()
        last_date_index = 0
        for date in dates_list:
            displayed_date = Date().filter(date)
            delta = relativedelta(today, displayed_date)
            if (
                delta.years >= 1 and
                (delta.months > 0 or delta.days > 0)
            ):
                break
            last_date_index += 1

        return last_date_index

    def is_deferred_query(self):
        # If we ask for too much history sometimes the site tells
        # us that the query will be answered at a later time and asks to
        # confirm, we want to avoid this behaviour
        self.browser.wait_until(AnyCondition(
            StablePageCondition(),
            VisibleXPath('//div[@role="dialog"]/div[@class="popupContent"]')
        ))
        return 'Votre requête sera traitée en différé' in CleanText('//div[@role="dialog"]/div[@class="popupContent"]')(self.doc)

    def download_transactions(self, last_date_index, retry=False):
        # Select chosen date
        if retry:
            el = self.driver.find_element_by_xpath('//input[contains(@placeholder, "De la date d\'arrêté")]')
            el.click()
            self.browser.wait_xpath_visible('//div[@id="VAADIN_COMBOBOX_OPTIONLIST"]')

        el = self.driver.find_element_by_xpath(
            '//div[@id="VAADIN_COMBOBOX_OPTIONLIST"]//div[contains(@class, "suggestmenu")]//tr[%d]/td' % last_date_index
        )
        el.click()
        self.browser.wait_xpath_invisible('//div[@id="VAADIN_COMBOBOX_OPTIONLIST"]')
        self.browser.wait_xpath_invisible('//p[contains(@class, "Notification-description")][contains(text(), "a bien été sélectionnée")]')
        # Submit search for this date
        self.driver.execute_script("document.getElementById('BTN_SEARCH').click()")

        if self.is_deferred_query():
            # Clicking no on the popup
            el = self.driver.find_element_by_xpath('//div[@role="dialog"]/div[@class="popupContent"]//div[contains(@class, "button-friendly") and .//span[text()="Non"]]',)
            el.click()
            self.browser.wait_xpath_invisible('//div[@role="dialog"]/div[@class="popupContent"]')
            raise DeferredQuery()

        # Get data
        self.browser.wait_until(AnyCondition(
            VisibleXPath('//div/a/img'),
            VisibleXPath('//p[contains(@class, "Notification-description")][contains(text(), "Aucune opération")]'),
        ))
        if self.doc.xpath('//div/a/img'):
            el = self.driver.find_element_by_xpath('//div/a/img')
            el.click()
            return True
        return False

    def get_error(self):
        return CleanText('//div[@id="labelQuestion"]')(self.doc)

    def click_retry_intercepted(self, el):
        # This error can happens when we click too fast, error messages can
        # stack up and hide the button.
        click_retry = retry(ElementClickInterceptedException, delay=1)(el.click)
        click_retry()

    def select_account(self, account):
        self.browser.wait_until(StablePageCondition())
        self.browser.wait_until(
            AnyCondition(
                VisibleXPath('//div[span[span[text()="Annuler la sélection"]]]'),
                VisibleXPath('//div[span[span[text()="Sélectionner une carte"]]]')
            )
        )

        if self.doc.xpath('//div[span[span[text()="Annuler la sélection"]]]'):
            # If this is present that means a card has already been selected,
            # so just click it to remove the selected card.
            el = self.driver.find_element_by_xpath('//div[span[span[contains(text(), "Annuler la sélection")]]]')
            self.click_retry_intercepted(el)

        self.browser.wait_until(StablePageCondition())

        if self.doc.xpath('//div[span[span[text()="Sélectionner une carte"]]]'):
            # If this is present (usually present the first time we come to that page
            # or after we clicked the 'Annuler la sélection' button), we need to
            # click it to display the inputs we want
            el = self.driver.find_element_by_xpath('//div[span[span[text()="Sélectionner une carte"]]]')
            self.click_retry_intercepted(el)

        self.browser.wait_until(StablePageCondition())
        self.browser.wait_xpath_visible('//div[span[text()="Numéro de prestation"]]/following-sibling::input')

        # Fill the input
        el = self.driver.find_element_by_xpath('//div[span[text()="Numéro de prestation"]]/following-sibling::input')
        el.send_keys(account._service_number)

        # Click the search button
        self.browser.wait_until(StablePageCondition())
        self.browser.wait_xpath_clickable('//div[not(contains(@class, "v-disabled")) and span[span[contains(text(), "Rechercher")]]]')

        el = self.driver.find_element_by_xpath('//div[span[span[text()="Rechercher"]]]')
        self.click_retry_intercepted(el)
        self.browser.wait_xpath_visible('//table[@role="grid"]/tbody')

        # Get the button of the right card (there might be multiple
        # card with the same service number) and click it
        el = self.driver.find_element_by_xpath(
            '//tbody/tr/td[1][following-sibling::td[contains(text(), "%s")]]//div[contains(@class, "btnGrid-action")]' % account._card_number
        )
        self.click_retry_intercepted(el)
        self.browser.wait_xpath_visible('//p[contains(@class, "Notification-description")][contains(text(), "a bien été sélectionnée")]')


class HistoryXlsPage(LoggedPage, XLSPage):
    HEADER = 5

    @method
    class iter_history(DictElement):
        class item(ItemElement):
            klass = Transaction

            obj_label = CleanText(Dict('raison sociale'))

            def obj_original_currency(self):
                currency = Currency(Dict('code devise origine'))(self)
                if currency == 'EUR':
                    return NotAvailable
                return currency

            def obj_original_amount(self):
                if Field('original_currency')(self):
                    return CleanDecimal.French(Dict('montant brut devise origine'), sign='-')(self)
                return NotAvailable

            obj_amount = CleanDecimal.French(Dict('montant imputé'), sign='-')

            obj_date = Date(Dict("date d'arrêté"), dayfirst=True)
            obj_rdate = Date(Dict('date de vente'), dayfirst=True)
            obj_type = Transaction.TYPE_DEFERRED_CARD
