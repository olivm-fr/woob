# Copyright(C) 2012 Kevin Pouget
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

from woob.tools.backend import BackendConfig
from woob.tools.value import ValueBackendPassword, ValueTransient
from woob_modules.banquepopulaire import BanquePopulaireModule

from .browser import CreditCooperatif


__all__ = ["CreditCooperatifModule"]


class CreditCooperatifModule(BanquePopulaireModule):
    NAME = "creditcooperatif"
    MAINTAINER = "Kevin Pouget"
    EMAIL = "weboob@kevin.pouget.me"
    DESCRIPTION = "Crédit Coopératif"
    LICENSE = "LGPLv3+"
    DEPENDENCIES = ("bandpopulaire",)

    CONFIG = BackendConfig(
        ValueBackendPassword("login", label="Identifiant", masked=False, regexp=r"[a-zA-Z0-9]+"),
        ValueBackendPassword("password", label="Mot de passe"),
        ValueTransient("code_sms", regexp=r"\d{8}"),
        ValueTransient("code_emv", regexp=r"\d{8}"),
        ValueTransient("resume"),
        ValueTransient("request_information"),
    )

    BROWSER = CreditCooperatif
