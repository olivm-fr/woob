# Copyright(C) 2012-2013 Romain Bignon
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

from woob.capabilities.bank import CapBankTransfer
from woob.capabilities.contact import CapContact
from woob_modules.cmso.module import CmsoModule

from .par.browser import CmbParBrowser
from .pro.browser import CmbProBrowser


__all__ = ['CmbModule']


class CmbModule(CmsoModule, CapBankTransfer, CapContact):
    NAME = 'cmb'
    MAINTAINER = 'Edouard Lambert'
    EMAIL = 'elambert@budget-insight.com'
    VERSION = '3.6'
    DEPENDENCIES = ('cmso',)
    DESCRIPTION = 'Crédit Mutuel de Bretagne'
    LICENSE = 'LGPLv3+'
    AVAILABLE_BROWSERS = {'par': CmbParBrowser, 'pro': CmbProBrowser}
