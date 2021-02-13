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

from .pages import LoginPage, DecoupledStatePage, CancelDecoupled
from weboob.browser.browsers import AbstractBrowser
from weboob.browser.profiles import Wget
from weboob.browser.url import URL

__all__ = ['CICBrowser']


class CICBrowser(AbstractBrowser):
    PROFILE = Wget()
    TIMEOUT = 30
    BASEURL = 'https://www.cic.fr'
    PARENT = 'creditmutuel'

    login = URL(
        r'/fr/authentification.html',
        r'/sb/fr/banques/particuliers/index.html',
        r'/(?P<subbank>.*)/fr/$',
        r'/(?P<subbank>.*)/fr/banques/accueil.html',
        r'/(?P<subbank>.*)/fr/banques/particuliers/index.html',
        LoginPage
    )

    decoupled_state = URL(r'/(?P<subbank>.*)fr/otp/SOSD_OTP_GetTransactionState.htm', DecoupledStatePage)
    cancel_decoupled = URL(r'/(?P<subbank>.*)fr/otp/SOSD_OTP_CancelTransaction.htm', CancelDecoupled)
