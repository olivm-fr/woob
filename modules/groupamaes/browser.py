# -*- coding: utf-8 -*-

# Copyright(C) 2014      Bezleputh
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

from woob.browser import URL
from woob_modules.cmes.browser import CmesBrowser

from .pages import LoginPage


__all__ = ['GroupamaesBrowser']


class GroupamaesBrowser(CmesBrowser):
    PARENT = 'cmes'

    login = URL(r'/groupama-es/(?P<client_space>.*)fr/identification/authentification.html', LoginPage)

    def __init__(self, config, login, password, baseurl, subsite, *args, **kwargs):
        self.woob = kwargs['woob']
        super(GroupamaesBrowser, self).__init__(config, login, password, baseurl, subsite, *args, **kwargs)
