# -*- coding: utf-8 -*-

# Copyright(C) 2019      olivm38
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

from weboob.tools.backend import Module, BackendConfig
from weboob.capabilities.gauge import CapGauge, GaugeSensor, Gauge, SensorNotFound
from weboob.capabilities.base import find_object, NotLoaded
from weboob.tools.value import Value, ValueBackendPassword

from .proxy_browser import ProxyBrowser

import json

__all__ = ['OzeModule']


class OzeModule(Module, CapGauge):
    NAME = 'oze'
    DESCRIPTION = 'oze website'
    MAINTAINER = 'olivm38'
    EMAIL = 'olivier@zron.fr'
    LICENSE = 'AGPLv3+'
    VERSION = '2.1'
    CONFIG = BackendConfig(Value('username', label='Username', regexp='.+'),
                           ValueBackendPassword('password', label='Password'))
    
    BROWSER = ProxyBrowser
    cachedGauges = NotLoaded

    def create_default_browser(self):
        return self.create_browser(self.config['username'].get(), self.config['password'].get())
    
    def load_cache(self):
        if self.cachedGauges == NotLoaded: 
            self.cachedGauges = self.browser.get_children_list()
            for gauge in self.cachedGauges:
                self.logger.debug("Getting authorisation")
                auth = self.browser.get_grade_auth(json.loads(gauge.city)['uai'])
                self.browser.get_grade_subjects(auth, gauge)
        
    
    def get_last_measure(self, id):
        """
        Get last measures of a sensor.

        :param id: ID of the sensor.
        :type id: str
        :rtype: :class:`GaugeMeasure`
        """
        if not isinstance(id, GaugeSensor):
            id = self._get_sensor_by_id(id)
        return id.lastvalue
    
    def iter_gauge_history(self, id):
        """
        Get history of a gauge sensor.

        :param id: ID of the gauge sensor
        :type id: str
        :rtype: iter[:class:`GaugeMeasure`]
        """
        if not isinstance(id, GaugeSensor):
            id = self._get_sensor_by_id(id)
        return iter(id.history)

    def iter_gauges(self, pattern=None):
        """
        Iter gauges.

        :param pattern: if specified, used to search gauges.
        :type pattern: str
        :rtype: iter[:class:`Gauge`]
        """
        self.load_cache()
        if pattern is None:
            for gauge in self.cachedGauges:
                yield gauge
        else:
            lowpattern = pattern.lower()
            for gauge in self.cachedGauges:
                if lowpattern in gauge.name.lower() or lowpattern in gauge.object.lower():
                    yield gauge

    def iter_sensors(self, id, pattern=None):
        """
        Iter instrument of a gauge.

        :param: ID of the gauge
        :param pattern: if specified, used to search sensors.
        :type pattern: str
        :rtype: iter[:class:`GaugeSensor`]
        """
        self.load_cache()
        if not isinstance(id, Gauge):
            id = find_object(self.cachedGauges, name=id, error=SensorNotFound)
        
        if pattern is None:
            for sensor in id.sensors:
                yield sensor
        else:
            lowpattern = pattern.lower()
            for sensor in id.sensors:
                if lowpattern in sensor.name.lower():
                    yield sensor

    def _get_sensor_by_id(self, id):
        for gauge in self.iter_gauges():
            for sensor in self.iter_sensors(gauge):
                if id == sensor.id:
                    return sensor
        raise SensorNotFound()

 
