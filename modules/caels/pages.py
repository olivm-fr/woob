# Copyright(C) 2018      Quentin Defenouillere
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

from woob.browser.elements import ItemElement, method, DictElement
from woob.browser.filters.standard import CleanDecimal, Date, Field, Env
from woob.browser.filters.json import Dict
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.base import NotAvailable
from woob.tools.capabilities.bank.investments import is_isin_valid
from woob_modules.amundi.pages import AccountsPage as AmundiAccountsPage


class AccountsPage(AmundiAccountsPage):
    @method
    class iter_investments(DictElement):
        def find_elements(self):
            for psds in Dict('listPositionsSalarieFondsDto')(self):
                for psd in psds.get('positionsSalarieDispositifDto'):
                    if psd.get('codeDispositif') == Env('account_id')(self):
                        return psd.get('positionsSalarieFondsDto')
            return {}

        class item(ItemElement):
            klass = Investment

            obj_label = Dict('libelleFonds')
            obj_unitvalue = Dict('vl') & CleanDecimal
            obj_quantity = Dict('nbParts') & CleanDecimal
            obj_valuation = Dict('mtBrut') & CleanDecimal
            obj_code = Dict('codeIsin', default=NotAvailable)
            obj_vdate = Date(Dict('dtVl'))
            # The "diff" is only present on the CAELS website but not its parent (amundi):
            obj_diff = Dict('mtPMV') & CleanDecimal

            def obj_code_type(self):
                if is_isin_valid(Field('code')(self)):
                    return Investment.CODE_TYPE_ISIN
                return NotAvailable
