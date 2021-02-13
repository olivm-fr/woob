# -*- coding: utf-8 -*-

# Copyright(C) 2010-2011 Julien Veyssier
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

from weboob.browser.browsers import AbstractBrowser
from weboob.browser.profiles import Wget
from weboob.browser.url import URL
from weboob.browser.browsers import need_login

from .pages import AdvisorPage, LoginPage, DecoupledStatePage, CancelDecoupled


__all__ = ['BECMBrowser']


class BECMBrowser(AbstractBrowser):
    PROFILE = Wget()
    TIMEOUT = 30
    BASEURL = 'https://www.becm.fr'
    PARENT = 'creditmutuel'

    login = URL('/fr/authentification.html', LoginPage)
    advisor = URL('/fr/banques/Details.aspx\?banque=.*', AdvisorPage)
    decoupled_state = URL(r'/(?P<subbank>.*)fr/otp/SOSD_OTP_GetTransactionState.htm', DecoupledStatePage)
    cancel_decoupled = URL(r'/(?P<subbank>.*)fr/otp/SOSD_OTP_CancelTransaction.htm', CancelDecoupled)

    @need_login
    def get_advisor(self):
        advisor = None
        if not self.is_new_website:
            self.accounts.stay_or_go(subbank=self.currentSubBank)
            if self.page.get_advisor_link():
                advisor = self.page.get_advisor()
                self.location(self.page.get_advisor_link()).page.update_advisor(advisor)
        else:
            advisor = self.new_accounts.stay_or_go(subbank=self.currentSubBank).get_advisor()
            link = self.page.get_agency()
            if link:
                self.location(link)
                self.page.update_advisor(advisor)
        return iter([advisor]) if advisor else iter([])
