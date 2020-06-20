# -*- coding: utf-8 -*-

# Copyright(C) 2013 Julien Veyssier
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from weboob.capabilities.recipe import CapRecipe, Recipe
from weboob.tools.backend import Module
from weboob.tools.compat import quote_plus

from .browser import AllrecipesBrowser


__all__ = ['AllrecipesModule']


class AllrecipesModule(Module, CapRecipe):
    NAME = 'allrecipes'
    MAINTAINER = u'Julien Veyssier'
    EMAIL = 'julien.veyssier@aiur.fr'
    VERSION = '2.1'
    DESCRIPTION = u'Allrecipes English recipe website'
    LICENSE = 'AGPLv3+'
    BROWSER = AllrecipesBrowser

    def get_recipe(self, id):
        return self.browser.get_recipe(id)

    def iter_recipes(self, pattern):
        return self.browser.iter_recipes(quote_plus(pattern.encode('utf-8')))

    def fill_recipe(self, recipe, fields):
        if 'nb_person' in fields or 'instructions' in fields or 'picture' in fields:
            recipe = self.browser.get_recipe(recipe.id, recipe)
        return recipe

    OBJECTS = {Recipe: fill_recipe}
