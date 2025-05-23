# Copyright(C) 2020 Johann Broudin
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


from woob.capabilities.audiostream import BaseAudioStream
from woob.capabilities.collection import CapCollection
from woob.capabilities.radio import CapRadio, Radio
from woob.tools.backend import Module

from .browser import VirginBrowser


__all__ = ["VirginRadioModule"]


class VirginRadioModule(Module, CapRadio, CapCollection):
    NAME = "virginradio"
    MAINTAINER = "Johann Broudin"
    EMAIL = "Johann.Broudin@6-8.fr"
    VERSION = "3.7"
    DESCRIPTION = "VirginRadio french radio"
    LICENSE = "AGPLv3+"
    BROWSER = VirginBrowser

    def get_radio(self, radio):
        if not isinstance(radio, Radio):
            radio = Radio(radio)

        r = self.browser.radio(radio.id)

        if r is None:
            return None

        radio.title = r["title"]

        radio.description = self.browser.description(r)

        stream_hls = BaseAudioStream(0)
        stream_hls.url = r["hls_source"]
        stream_hls.bitrate = 135
        stream_hls.format = "aac"
        stream_hls.title = f"{stream_hls.format} {stream_hls.bitrate}kbits/s"

        stream = BaseAudioStream(0)
        stream.url = r["source"]
        stream.bitrate = 128
        stream.format = "mp3"
        stream.title = f"{stream.format} {stream.bitrate}kbits/s"

        radio.streams = [stream_hls, stream]
        radio.current = self.browser.current(r)

        return radio

    def iter_resources(self, objs, split_path):
        if Radio in objs:
            self._restrict_level(split_path)

            radios = self.browser.radios()

            for id in radios:
                yield self.get_radio(id)

    def iter_radios_search(self, pattern):
        for radio in self.iter_resources((Radio,), []):
            if pattern.lower() in radio.title.lower() or pattern.lower() in radio.description.lower():
                yield radio

    def fill_radio(self, radio, fields):
        if "current" in fields:
            if not radio.current:
                radio = self.get_radio(radio.id)
        return radio

    OBJECTS = {Radio: fill_radio}
