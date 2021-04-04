# -*- coding: utf-8 -*-

# Copyright(C) 2018      Phyks (Lucas Verney)
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

from __future__ import unicode_literals


from weboob.browser import PagesBrowser, URL

from .pages import RecipePage, SearchPage


class JournaldesfemmesBrowser(PagesBrowser):
    BASEURL = 'http://cuisine.journaldesfemmes.fr'

    recipe = URL(r'/recette/(?P<id>.+)', RecipePage)
    search = URL(r'/s/\?f_recherche=(?P<search>.+)', SearchPage)

    def get_recipe(self, id, obj=None):
        return self.recipe.go(id=id).get_recipe(obj=obj)

    def get_comments(self, id):
        return self.recipe.stay_or_go(id=id).get_comments()

    def search_recipes(self, pattern):
        return self.search.go(search=pattern).get_recipes()
