# -*- coding: utf-8 -*-

# Copyright(C) 2014      smurail
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
import re

from dateutil.relativedelta import relativedelta

from weboob.tools.capabilities.bank.transactions import sorted_transactions
from weboob.capabilities.base import find_object
from weboob.capabilities.bank import Account
from weboob.exceptions import BrowserIncorrectPassword, ActionNeeded
from weboob.browser import LoginBrowser, URL, need_login
from weboob.browser.exceptions import ServerError
from weboob.tools.date import LinearDateGuesser
from weboob.tools.compat import urlparse, parse_qsl

from .pages import (
    LoginPage, PasswordCreationPage, AccountsPage, HistoryPage, SubscriptionPage, InvestmentPage,
    InvestmentAccountPage, UselessPage, SSODomiPage, AuthCheckUser, ErrorPage,
)
from ..par.pages import ProfilePage


class CmsoProBrowser(LoginBrowser):
    login = URL(r'https://api.(?P<website>[\w.]+)/oauth-implicit/token', LoginPage)
    subscription = URL(r'https://api.(?P<website>[\w.]+)/domiapi/oauth/json/accesAbonnement', SubscriptionPage)
    accounts = URL(
        r'/domiweb/prive/professionnel/situationGlobaleProfessionnel/0-situationGlobaleProfessionnel.act',
        AccountsPage
    )
    history = URL(
        r'/domiweb/prive/professionnel/situationGlobaleProfessionnel/1-situationGlobaleProfessionnel.act',
        HistoryPage
    )
    password_creation = URL(
        r'/domiweb/prive/particulier/modificationMotDePasse/0-creationMotDePasse.act',
        PasswordCreationPage
    )
    useless = URL(r'/domiweb/prive/particulier/modificationMotDePasse/0-expirationMotDePasse.act', UselessPage)
    investment = URL(r'/domiweb/prive/particulier/portefeuilleSituation/0-situationPortefeuille.act', InvestmentPage)
    invest_account = URL(
        r'/domiweb/prive/particulier/portefeuilleSituation/2-situationPortefeuille.act\?(?:csrf=[^&]*&)?indiceCompte=(?P<idx>\d+)&idRacine=(?P<idroot>\d+)',
        InvestmentAccountPage
    )
    error = URL(r'https://pro.(?P<website>[\w.]+)/auth/errorauthn', ErrorPage)
    profile = URL(r'https://api.(?P<website>[\w.]+)/domiapi/oauth/json/edr/infosPerson', ProfilePage)
    ssoDomiweb = URL(r'https://api.(?P<website>[\w.]+)/domiapi/oauth/json/ssoDomiwebEmbedded', SSODomiPage)
    auth_checkuser = URL(r'https://api.(?P<website>[\w.]+)/securityapi/checkuser', AuthCheckUser)

    filter_page = URL(r'https://pro.(?P<website>[\w.]+)/espace/filter')

    arkea = '03'

    def __init__(self, website, config, *args, **kwargs):
        super(CmsoProBrowser, self).__init__(*args, **kwargs)

        self.BASEURL = "https://www.%s" % website
        self.website = website
        self.areas = []
        self.curr_area = None
        self.last_csrf = None
        self.name = website.split('.')[0]
        # This ids can be found pro.{website}/mabanque/config-XXXXXX.js
        self.client_id = 'nMdBJgaYgVaT67Ysf7XvTS9ayr9fdI69'

    def get_login_data(self):
        return {
            'accessCode': self.username,
            'password': self.password,
            'client_id': self.client_id,
            'responseType': 'token',
            'clientId': 'com.arkea.sitemobilepro.%s' % self.name,
            'redirectUri': 'https://pro.%s/auth/checkuser' % self.website,
            'errorUri': 'https://pro.%s/auth/errorauthn' % self.website,
            'fingerprint': 'b61a924d1245beb7469fef44db132e96',
        }

    def do_login(self):
        self.login.go(data=self.get_login_data(), website=self.website)
        if self.error.is_here():
            if 'INVALID_CREDENTIALS' in self.url:
                raise BrowserIncorrectPassword()
            raise Exception('Login error not handled: %r' % (urlparse(self.url).fragment,))

        hidden_params = dict(parse_qsl(urlparse(self.url).fragment))
        if hidden_params.get('scope') == 'consent':
            raise ActionNeeded('Vous devez réaliser la double authentification sur le portail internet')
        assert 'access_token' in hidden_params, 'Could not retrieve csrf token'

        self.session.headers.update({
            'Authorization': "Bearer %s" % hidden_params['access_token'],
            'X-ARKEA-EFS': self.arkea,
            'X-Csrf-Token': hidden_params['access_token'],
            'X-REFERER-TOKEN': 'RWDPRO',
        })

        self.auth_checkuser.go(json={"espaceApplication": "PRO", "espacePRO": "PRO"}, website=self.website)

        if self.useless.is_here():
            # user didn't change his password for 6 months and website ask us if we want to change it
            # just skip it by calling this url
            self.location('https://pro.%s/mabanque/pro/comptes/comptes' % self.website, method='POST')

        if self.password_creation.is_here():
            # user got a temporary password and never changed it, website ask to set a new password before grant access
            raise ActionNeeded(self.page.get_message())

        self.fetch_areas()

    def fetch_areas(self):
        if not self.areas:
            self.subscription.go(
                json={'includePart': False},
                website=self.website,
            )

            for sub in self.page.get('listAbonnement'):
                self.areas.append({
                    'contract': sub['numContratBAD'],
                    'id': sub['numeroPersonne'],
                })

    def go_with_ssodomi(self, path):
        if isinstance(path, URL):
            path = path.urls[0]
        if path.startswith('/domiweb'):
            path = path[len('/domiweb'):]

        json = {
            'rwdStyle': 'true',
            'service': path,
        }

        # Prevent an error 403
        self.filter_page.go(website=self.website)

        url = self.ssoDomiweb.go(
            website=self.website,
            headers={'ADRIM': 'isAjax:true'},
            json=json).get_sso_url()

        page = self.location(url).page
        # each time we get a new csrf we store it because it can be used in further navigation
        self.last_csrf = self.url.split('csrf=')[1]
        return page

    def go_on_area(self, area):
        if self.curr_area == area:
            return

        # The website raise an error 403 if we try to change the area without doing this call first.
        self.filter_page.go(website=self.website)

        ret = self.location(
            'https://api.%s/securityapi/changeSpace' % (self.website),
            json={
                'clientIdSource': self.client_id,
                'espaceDestination': 'PRO',
                'fromMobile': False,
                'numContractDestination': area['contract'],
            }).json()
        # Csrf is updated each time we change area
        self.session.headers.update({
            'Authorization': "Bearer %s" % ret['accessToken'],
            'X-Csrf-Token': ret['accessToken'],
        })
        self.curr_area = area

    @need_login
    def iter_accounts(self):
        self.fetch_areas()

        # Manage multiple areas
        if not self.areas:
            raise BrowserIncorrectPassword("Vous n'avez pas de comptes sur l'espace professionnel de ce site.")

        seen = set()
        for area in self.areas:
            self.go_on_area(area)
            try:
                account_page = self.go_with_ssodomi(self.accounts)
                for a in account_page.iter_accounts():
                    if a.type == Account.TYPE_MARKET:
                        # for legacy reason we have to get id on investment page for market account
                        account_page = self.go_with_ssodomi(self.investment)
                        assert self.investment.is_here()

                        for inv_account in self.page.iter_accounts():
                            if self._match_account_ids(a.id, inv_account.id):
                                a.id = inv_account.id
                                break

                    seenkey = (a.id, a._owner)
                    if seenkey in seen:
                        self.logger.warning('skipping seemingly duplicate account %r', a)
                        continue

                    a._area = area
                    seen.add(seenkey)
                    yield a
            except ServerError:
                self.logger.warning('Area unavailable.')

    def _build_next_date_range(self, date_range):
        date_format = '%d/%m/%Y'

        last_day = datetime.datetime.strptime(date_range[10:], date_format)
        first_day = last_day + datetime.timedelta(days=1)
        last_day = first_day + relativedelta(months=1, days=-1)

        first_str = datetime.datetime.strftime(first_day, date_format)
        last_str = datetime.datetime.strftime(last_day, date_format)
        return first_str + last_str

    @need_login
    def iter_history(self, account):
        if account._history_url.startswith('javascript:') or account._history_url == '#':
            raise NotImplementedError()

        account = find_object(self.iter_accounts(), id=account.id)
        # this url (reached with a GET) return some transactions, but not in same format than POST method
        # and some transactions are duplicated and other are missing, don't take them from GET
        # because we don't want to manage both way in iter_history

        # fetch csrf token
        self.go_with_ssodomi(self.accounts)
        # we have to update the url at this moment because history consultation has to follow immediatly accounts page consultation.
        account._history_url = self.update_csrf_token(account._history_url)

        self.location(account._history_url)
        date_range_list = self.page.get_date_range_list()

        # a date_range is a couple of date like '01/03/201831/03/2018' but current month is often missing and we have to rebuild it
        # from first one to get very recent transaction without scrap them from 1st page (reached with GET url)
        if len(date_range_list):
            date_range_list = [self._build_next_date_range(date_range_list[0])] + date_range_list

        for date_range in date_range_list:
            date_guesser = LinearDateGuesser(datetime.datetime.strptime(date_range[10:], "%d/%m/%Y"))
            try:
                self.location(account._history_url, data={'date': date_range})
            except ServerError as error:
                if error.response.status_code == 500:
                    if 'RELEVE NON DISPONIBLE A CETTE PERIODE' in error.response.text:
                        continue
                        # just skip because it's still possible to have transactions next months
                        # Yes, they really did that heresy...
                    else:
                        raise
            for tr in sorted_transactions(self.page.iter_history(date_guesser=date_guesser)):
                yield tr

    def update_csrf_token(self, history_url):
        return re.sub('(?<=csrf=)[0-9a-zA-Z]+', self.last_csrf, history_url)

    @need_login
    def iter_coming(self, account):
        raise NotImplementedError()

    def _match_account_ids(self, account_page_id, investment_page_id):
        # account id in investment page, is a little bit different from the account page
        # some part of id have swapped and one other (with two digit) is not present
        # if account_page_id is 222223333311111111144 then investment_page_id will be 111111111.33333xx
        number, _id = investment_page_id.split('.')
        _id = _id[:-2] + number

        return _id in account_page_id

    @need_login
    def iter_investment(self, account):
        self.go_on_area(account._area)

        self.go_with_ssodomi(self.investment)
        assert self.investment.is_here()
        for page_account in self.page.iter_accounts():
            if account.id == page_account.id:
                if page_account._formdata:
                    self.page.go_account(*page_account._formdata)
                else:
                    self.location(page_account.url)
                break
        else:
            # not an investment account
            return []

        if self.investment.is_here():
            assert self.page.has_error()
            self.logger.warning('account %r does not seem to be usable', account)
            return []

        assert self.invest_account.is_here()
        return self.page.iter_investments()

    @need_login
    def get_profile(self):
        self.go_on_area(self.areas[0])

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'ADRIM': 'isAjax:true',
        }

        # Prevent an error 403
        self.filter_page.go(website=self.website)

        return self.profile.go(
            website=self.website,
            json={},
            headers=headers).get_profile()
