# Copyright(C) 2010-2011 Julien Veyssier
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

# flake8: compatible

from woob_modules.creditmutuel.browser import CreditMutuelBrowser

__all__ = ['CICBrowser']


class CICBrowser(CreditMutuelBrowser):
    BASEURL = 'https://www.cic.fr'

    login = CreditMutuelBrowser.login.with_urls(
        r'/fr/authentification.html',
        r'/sb/fr/banques/particuliers/index.html',
        r'/(?P<subbank>.*)/fr/$',
        r'/(?P<subbank>.*)/fr/banques/accueil.html',
        r'/(?P<subbank>.*)/fr/banques/particuliers/index.html',
    )

    decoupled_state = CreditMutuelBrowser.decoupled_state.with_urls(
        r'/(?P<subbank>.*)fr/otp/SOSD_OTP_GetTransactionState.htm',
    )
    cancel_decoupled = CreditMutuelBrowser.cancel_decoupled.with_urls(
        r'/(?P<subbank>.*)fr/otp/SOSD_OTP_CancelTransaction.htm',
    )
