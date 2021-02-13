# -*- coding: utf-8 -*-

# Copyright(C) 2015      Vincent Paredes
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

from weboob.browser import LoginBrowser, AbstractBrowser, URL, need_login
from weboob.exceptions import BrowserIncorrectPassword, RecaptchaV2Question

from .pages import HomePage, LoginPage, ProBillsPage, DocumentsPage


class LdlcParBrowser(AbstractBrowser):
    PARENT = 'materielnet'
    BASEURL = 'https://secure2.ldlc.com'

    documents = URL(r'/fr-fr/Orders/PartialCompletedOrdersHeader', DocumentsPage)

    def __init__(self, config, *args, **kwargs):
        super(LdlcParBrowser, self).__init__(config, *args, **kwargs)
        self.config = config
        self.lang = 'fr-fr/'

    @need_login
    def iter_documents(self, subscription):
        # the request need POST method
        json_response = self.location('/fr-fr/Orders/CompletedOrdersPeriodSelection', data={}).json()

        for data in json_response:
            for doc in self.location('/fr-fr/Orders/PartialCompletedOrdersHeader', data=data).page.get_documents(subid=subscription.id):
                yield doc


class LdlcBrowser(LoginBrowser):
    login = URL(r'/Account/LoginPage.aspx', LoginPage)
    home = URL(r'/$', HomePage)

    def __init__(self, config, *args, **kwargs):
        super(LdlcBrowser, self).__init__(*args, **kwargs)
        self.config = config

    def do_login(self):
        self.login.stay_or_go()
        sitekey = self.page.get_recaptcha_sitekey()
        if sitekey and not self.config['captcha_response'].get():
            raise RecaptchaV2Question(website_key=sitekey, website_url=self.login.build())

        self.page.login(self.username, self.password, self.config['captcha_response'].get())

        if self.login.is_here():
            raise BrowserIncorrectPassword(self.page.get_error())

    @need_login
    def get_subscription_list(self):
        return self.home.stay_or_go().get_subscriptions()


class LdlcProBrowser(LdlcBrowser):
    BASEURL = 'https://secure.ldlc-pro.com'

    bills = URL(r'/Account/CommandListingPage.aspx', ProBillsPage)

    @need_login
    def iter_documents(self, subscription):
        self.bills.go()
        hidden_field = self.page.get_ctl00_actScriptManager_HiddenField()

        for value in self.page.get_range():
            data = {
                'ctl00$cphMainContent$ddlDate': value,
                'ctl00$actScriptManager': 'ctl00$cphMainContent$ddlDate',
                '__EVENTTARGET': 'ctl00$cphMainContent$ddlDate',  # order them by date, very important for download
                'ctl00$cphMainContent$hfTypeTri': 1,
            }
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
                'x-microsoftajax': 'Delta=true',  # without it, it can return 500 (sometimes)
            }
            self.bills.go(data=data, headers=headers)
            view_state = self.page.get_view_state()
            # we need position to download file
            position = 1
            for bill in self.page.iter_documents(subid=subscription.id):
                bill._position = position
                bill._view_state = view_state
                bill._hidden_field = hidden_field
                position += 1
                yield bill

    @need_login
    def download_document(self, bill):
        data = {
            '__EVENTARGUMENT': '',
            '__EVENTTARGET': '',
            '__LASTFOCUS': '',
            '__SCROLLPOSITIONX': 0,
            '__SCROLLPOSITIONY': 0,
            '__VIEWSTATE': bill._view_state,
            'ctl00$actScriptManager': '',
            'ctl00$cphMainContent$DetailCommand$hfCommand': '',
            'ctl00$cphMainContent$DetailCommand$txtAltEmail': '',
            'ctl00$cphMainContent$ddlDate': bill.date.year,
            'ctl00$cphMainContent$hfCancelCommandId': '',
            'ctl00$cphMainContent$hfCommandId': '',
            'ctl00$cphMainContent$hfCommandSearch': '',
            'ctl00$cphMainContent$hfOrderTri': 1,
            'ctl00$cphMainContent$hfTypeTri': 1,
            'ctl00$cphMainContent$rptCommand$ctl%s$hlFacture.x' % str(bill._position).zfill(2): '7',
            'ctl00$cphMainContent$rptCommand$ctl%s$hlFacture.y' % str(bill._position).zfill(2): '11',
            'ctl00$cphMainContent$txtCommandSearch': '',
            'ctl00$hfCountries': '',
            'ctl00$ucHeaderControl$ctrlSuggestedProductPopUp$HiddenCommandeSupplementaire': '',
            'ctl00$ucHeaderControl$ctrlSuggestedProductPopUp$hiddenPopUp': '',
            'ctl00$ucHeaderControl$txtSearch': 'Rechercher+...',
            'ctl00_actScriptManager_HiddenField': bill._hidden_field
        }

        return self.open(bill.url, data=data).content
