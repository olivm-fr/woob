# Copyright(C) 2019 Sylvie Ye
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from .login import LoginPage
from .accounts_page import (
    AccountsPage, HistoryPage, ComingPage, LifeInsurancePage, InvestTokenPage,
    AccountInfoPage,
)
from .transfer_page import (
    DebitAccountsPage, CreditAccountsPage, TransferPage, AddRecipientPage,
    OtpChannelsPage, ConfirmOtpPage,
)
from .profile_page import ProfilePage

__all__ = [
    'LoginPage', 'AccountsPage', 'HistoryPage', 'ComingPage', 'AccountInfoPage',
    'InvestTokenPage', 'LifeInsurancePage',
    'DebitAccountsPage', 'CreditAccountsPage', 'TransferPage',
    'AddRecipientPage', 'OtpChannelsPage', 'ConfirmOtpPage',
    'ProfilePage',
]
