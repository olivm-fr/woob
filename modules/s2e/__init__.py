# -*- coding: utf-8 -*-

# Copyright(C) 2015 Christophe Lampin
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


from .module import S2eModule

from .browser import CapeasiBrowser, ErehsbcBrowser, BnppereBrowser, EsaliaBrowser, CreditdunordpeeBrowser

__all__ = ['S2eModule', 'CapeasiBrowser', 'ErehsbcBrowser', 'BnppereBrowser', 'EsaliaBrowser', 'CreditdunordpeeBrowser']
