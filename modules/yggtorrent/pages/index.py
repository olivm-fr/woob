# -*- coding: utf-8 -*-

# Copyright(C) 2018 Julien Veyssier
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


from woob.browser.pages import HTMLPage, RawPage


class LoginPage(RawPage):
    pass

class HomePage(HTMLPage):
    def login(self, login, password):
        form = self.get_form(xpath='//form[@action="%suser/login"]' % self.browser.BASEURL)
        form['id'] = login
        form['pass'] = password
        form.submit(format_url='utf-8')
    @property
    def logged(self):
        return bool(self.doc.xpath('//a[@href="%suser/logout"]' % self.browser.BASEURL))

