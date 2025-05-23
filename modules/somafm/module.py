# Copyright(C) 2013 Roger Philibert
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

from woob.capabilities.collection import CapCollection
from woob.capabilities.radio import CapRadio, Radio
from woob.tools.backend import Module

from .browser import SomaFMBrowser


__all__ = ["SomaFMModule"]


class SomaFMModule(Module, CapRadio, CapCollection):
    NAME = "somafm"
    MAINTAINER = "Roger Philibert"
    EMAIL = "roger.philibert@gmail.com"
    VERSION = "3.7"
    DESCRIPTION = "SomaFM web radio"
    LICENSE = "AGPLv3+"
    BROWSER = SomaFMBrowser

    def iter_radios_search(self, pattern):
        pattern = pattern.lower()
        for radio in self.browser.iter_radios():
            if pattern in radio.title.lower() or pattern in radio.description.lower():
                yield radio

    def iter_resources(self, objs, split_path):
        if Radio in objs:
            self._restrict_level(split_path)

            yield from self.browser.iter_radios()

    def get_radio(self, radio_id):
        for radio in self.browser.iter_radios():
            if radio_id == radio.id:
                return radio

    def fill_radio(self, radio, fields):
        if "current" in fields:
            return self.get_radio(radio.id)
        return radio

    OBJECTS = {Radio: fill_radio}
