# -*- coding: utf-8 -*-

# Copyright(C) 2010-2013 Romain Bignon, Julien Hébert
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


import sys
from datetime import datetime

from weboob.capabilities.base import Currency, empty
from weboob.capabilities.travel import ICapTravel, RoadmapFilters
from weboob.tools.application.repl import ReplApplication, defaultcount
from weboob.tools.application.formatters.iformatter import PrettyFormatter


__all__ = ['Traveloob']


class DeparturesFormatter(PrettyFormatter):
    MANDATORY_FIELDS = ('id', 'type', 'departure_station', 'arrival_station', 'time')

    def get_title(self, obj):
        s = obj.type
        if hasattr(obj, 'price') and not empty(obj.price):
            s += u' %s %s' % (self.colored(u'—', 'cyan'), self.colored('%6.2f %s' % (obj.price, Currency.currency2txt(obj.currency)), 'green'))
        return s

    def get_description(self, obj):
        if hasattr(obj, 'arrival_time') and not empty(obj.arrival_time):
            s = '(%s)  %s\n\t(%s)  %s' % (self.colored(obj.time.strftime('%H:%M'), 'cyan'),
                                          obj.departure_station,
                                          self.colored(obj.arrival_time.strftime('%H:%M'), 'cyan'),
                                          obj.arrival_station)
        else:
            s = '(%s)  %20s -> %s' % (self.colored(obj.time.strftime('%H:%M'), 'cyan'),
                                      obj.departure_station, obj.arrival_station)

        return s

class StationsFormatter(PrettyFormatter):
    MANDATORY_FIELDS = ('id', 'name')

    def get_title(self, obj):
        return obj.name

class Traveloob(ReplApplication):
    APPNAME = 'traveloob'
    VERSION = '0.h'
    COPYRIGHT = 'Copyright(C) 2010-2013 Romain Bignon'
    DESCRIPTION = "Console application allowing to search for train stations and get departure times."
    SHORT_DESCRIPTION = "search for train stations and departures"
    CAPS = ICapTravel
    DEFAULT_FORMATTER = 'table'
    EXTRA_FORMATTERS = {'stations': StationsFormatter,
                        'departures': DeparturesFormatter,
                       }
    COMMANDS_FORMATTERS = {'stations':     'stations',
                           'departures':   'departures',
                          }

    def add_application_options(self, group):
        group.add_option('--departure-time')
        group.add_option('--arrival-time')

    @defaultcount(10)
    def do_stations(self, pattern):
        """
        stations PATTERN

        Search stations.
        """
        for backend, station in self.do('iter_station_search', pattern):
            self.format(station)

    @defaultcount(10)
    def do_departures(self, line):
        """
        departures STATION [ARRIVAL [DATE]]]

        List all departures for a given station.
        """
        station, arrival, date = self.parse_command_args(line, 3, 1)

        station_id, backend_name = self.parse_id(station)
        if arrival:
            arrival_id, backend_name2 = self.parse_id(arrival)
            if backend_name and backend_name2 and backend_name != backend_name2:
                print >>sys.stderr, 'Departure and arrival aren\'t on the same backend'
                return 1
        else:
            arrival_id = backend_name2 = None

        if backend_name:
            backends = [backend_name]
        elif backend_name2:
            backends = [backend_name2]
        else:
            backends = None

        if date is not None:
            try:
                date = self.parse_datetime(date)
            except ValueError as e:
                print >>sys.stderr, 'Invalid datetime value: %s' % e
                print >>sys.stderr, 'Please enter a datetime in form "yyyy-mm-dd HH:MM" or "HH:MM".'
                return 1

        for backend, departure in self.do('iter_station_departures', station_id, arrival_id, date, backends=backends):
            self.format(departure)

    def do_roadmap(self, line):
        """
        roadmap DEPARTURE ARRIVAL

        Display the roadmap to travel from DEPARTURE to ARRIVAL.

        Command-line parameters:
           --departure-time TIME    requested departure time
           --arrival-time TIME      requested arrival time

        TIME might be in form "yyyy-mm-dd HH:MM" or "HH:MM".

        Example:
            > roadmap Puteaux Aulnay-sous-Bois --arrival-time 22:00
        """
        departure, arrival = self.parse_command_args(line, 2, 2)

        filters = RoadmapFilters()
        try:
            filters.departure_time = self.parse_datetime(self.options.departure_time)
            filters.arrival_time = self.parse_datetime(self.options.arrival_time)
        except ValueError as e:
            print >>sys.stderr, 'Invalid datetime value: %s' % e
            print >>sys.stderr, 'Please enter a datetime in form "yyyy-mm-dd HH:MM" or "HH:MM".'
            return 1

        for backend, route in self.do('iter_roadmap', departure, arrival, filters):
            self.format(route)

    def parse_datetime(self, text):
        if text is None:
            return None

        try:
            date = datetime.strptime(text, '%Y-%m-%d %H:%M')
        except ValueError:
            try:
                date = datetime.strptime(text, '%H:%M')
            except ValueError:
                raise ValueError(text)
            date = datetime.now().replace(hour=date.hour, minute=date.minute)

        return date
