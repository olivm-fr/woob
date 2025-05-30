# Copyright(C) 2013-2021      Romain Bignon
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


import datetime

from dateutil.relativedelta import relativedelta

from woob.browser.browsers import LoginBrowser, need_login
from woob.browser.url import URL
from woob.exceptions import BrowserHTTPError, BrowserIncorrectPassword, BrowserUnavailable

from .pages import (
    AccountPage,
    ErrorPage,
    HistoryDetailsPage,
    HomePage,
    InfoPage,
    LandingPage,
    LoginPage,
    OldWebsitePage,
    PartHistoryPage,
    ProHistoryPage,
    PromoPage,
    UselessPage,
)


__all__ = ["Paypal"]


class Paypal(LoginBrowser):
    BASEURL = "https://www.paypal.com"

    login = URL(
        r"https://\w+\.paypal\.com/signin.*",
        r"/cgi-bin/webscr\?cmd=_login-submit.+$",
        "/fr/webapps/mpp/home",
        LoginPage,
    )
    landing = URL("/home", r"/(?!myaccount)\w+/home", r"/$", LandingPage)  # locale home
    useless = URL(
        r"/cgi-bin/webscr\?cmd=_login-processing.+$",
        r"/cgi-bin/webscr\?cmd=_account.*$",
        r"/cgi-bin/webscr\?cmd=_login-done.+$",
        UselessPage,
    )
    info = URL("/fr/merchantsignup/personalInfo", InfoPage)
    home = URL(
        r".*/cgi-bin/webscr\?cmd=_home&country_lang.x=true$",
        r"https://\w+.paypal.com/webapps/business/\?country_lang.x=true",
        r"https://\w+.paypal.com/myaccount/\?nav=0.0",
        r"https://\w+.paypal.com/webapps/business/\?nav=0.0",
        r"https://\w+.paypal.com/myaccount/$",
        r"/businessexp/summary.*",
        r"/myaccount/?\?country_lang.x=true",
        "/businessexp/fees/interchange-fees",
        "/mep/dashboard",
        "/myaccount/home",
        HomePage,
    )
    error = URL("/auth/validatecaptcha$", ErrorPage)
    history_details = URL(
        r"https://\w+\.paypal\.com/cgi-bin/webscr\?cmd=_history-details-from-hub&id=[\-A-Z0-9]+$",
        r"https://\w+\.paypal\.com/myaccount/transaction/details/[\-A-Z0-9]+$",
        HistoryDetailsPage,
    )
    promo = URL(
        r"https://www\.paypal\.com/fr/webapps/mpp/clickthru/paypal-app-promo-2.*",
        "/fr/webapps/mpp/clickthru.*",
        PromoPage,
    )
    account = URL(
        "https://www.paypal.com/myaccount/money",
        "https://www.paypal.com/businessexp/money",
        "https://www.paypal.com/webapps/business/money",
        AccountPage,
    )
    pro_history = URL(r"https://\w+\.paypal\.com/businessexp/transactions/activity\?.*", ProHistoryPage)
    part_history = URL(r"https://\w+\.paypal\.com/myaccount/(activity|transactions)/.*", PartHistoryPage)
    old_website = URL("https://paypalmanager.paypal.com/login.do", OldWebsitePage)

    TIMEOUT = 180.0

    def __init__(self, *args, **kwargs):
        self.BEGINNING = datetime.date.today() - relativedelta(months=24)
        self.account_type = None
        self.account_currencies = list()
        super().__init__(*args, **kwargs)

    def do_login(self):
        assert isinstance(self.username, str)
        assert isinstance(self.password, str)

        if not self.login.is_here():
            self.location("/signin/")

        response = self.open(self.page.get_script_url())
        token, csrf, key, value, sessionID, cookie = self.page.get_token_and_csrf(response.text)

        self.session.cookies.update({"xppcts": cookie})
        data = {}
        data["ads_token_js"] = token
        data["_csrf"] = csrf
        data["_sessionID"] = sessionID
        data[key] = value
        res = self.open("/auth/verifychallenge", data=data)
        if not "OK" in res.text:
            raise BrowserUnavailable("Challenge failed")

        res = self.page.login(self.username, self.password)

        if "LoginFailed" in res.text or "Sorry, we can't log you in" in res.text or self.error.is_here():
            raise BrowserIncorrectPassword()

        if "/auth/validatecaptcha" in res.text:
            raise BrowserUnavailable("captcha")

        self.location("/")
        if self.old_website.is_here():
            self.location("https://www.paypal.com/businessexp/summary")
        if self.login.is_here() or self.landing.is_here():
            raise BrowserUnavailable("login failed")
        self.detect_account_type()

    def detect_account_type(self):
        if self.page:
            self.page.detect_account_type()

    @need_login
    def get_accounts(self):
        if self.account_type is None:
            self.detect_account_type()
        self.account.stay_or_go()
        return self.page.get_accounts()

    @need_login
    def get_account(self, _id):
        self.account.stay_or_go()

        return self.page.get_account(_id)

    @need_login
    def get_personal_history(self, account):
        s = self.BEGINNING.strftime("%Y-%m-%d")
        e = datetime.date.today().strftime("%Y-%m-%d")
        data = {
            "transactionType": "ALL",
            "timeFrame": "90",
            "nextPageToken": "",
            "freeTextSearch": "",
            "startDate": s,
            "endDate": e,
        }
        # The response is sometimes not the one we expect.
        exc = None
        for i in range(3):
            try:
                self.location(
                    "https://www.paypal.com/myaccount/activity/filter?%s",
                    params=data,
                    headers={"Accept": "application/json, text/javascript, */*; q=0.01"},
                )
                if self.page.transaction_left():
                    return self.page.iter_transactions(account)
                return iter([])
            except KeyError as e:
                self.logger.warning("retrying to get activity ...")
                exc = e
        raise exc

    @need_login
    def get_download_history(self, account, step_min=None, step_max=None):
        if self.account_type == "perso":
            yield from self.get_personal_history(account)
        else:
            if step_min is None and step_max is None:
                step_min = 30
                step_max = 180

            def fetch_fn(start, end):
                p = self.download_history(start, end)
                transactions = []
                # Iter on each page
                while self.location(
                    "https://www.paypal.com/businessexp/transactions/activity", params=p
                ).page.transaction_left():
                    p["next_page_token"] = self.page.get_next_page_token()
                    for t in self.page.iter_transactions(account):
                        transactions.append(t)
                    if not p["next_page_token"]:
                        break
                return transactions if len(transactions) else iter([])

            assert step_max <= 365 * 2  # PayPal limitations as of 2014-06-16
            try:
                yield from self.smart_fetch(
                    beginning=self.BEGINNING,
                    end=datetime.date.today(),
                    step_min=step_min,
                    step_max=step_max,
                    fetch_fn=fetch_fn,
                )
            except BrowserHTTPError:
                self.logger.warning("Paypal timeout")

    def smart_fetch(self, beginning, end, step_min, step_max, fetch_fn):
        """
        Fetches transactions in small chunks to avoid request timeouts.
        Time period of each requested chunk is adjusted dynamically.
        """
        FACTOR = 1.5
        step = step_min
        while end > beginning:
            start = end - datetime.timedelta(step)
            chunk = list(fetch_fn(start, end))
            end = start - datetime.timedelta(1)
            if len(chunk) > 40:
                # If there're too much transactions in current period, decrease
                # the period.
                step = max(step_min, step / FACTOR)
            else:
                # If there's no transactions, or only a bit, in current period,
                # increase the period.
                step = min(step_max, step * FACTOR)
            yield from chunk

    def download_history(self, start, end):
        """
        Download history.
        However, it is not normalized, and sometimes the download is refused
        and sent later by mail.
        """
        params = {
            "transactiontype": "ALL_TRANSACTIONS",
            "currency": "ALL_TRANSACTIONS_CURRENCY",
            "limit": "",
            "archive": "ACTIVE_TRANSACTIONS",
            "fromdate_year": start.year,
            "fromdate_month": start.month - 1,  # Months are from 0 to 11.
            "fromdate_day": start.day,
            "todate_year": end.year,
            "todate_month": end.month - 1,
            "todate_day": end.day,
        }
        return params

    def transfer(self, from_id, to_id, amount, reason=None):
        raise NotImplementedError()
