# Copyright(C) 2013 Julien Veyssier
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

from woob.capabilities.recipe import CapRecipe, Recipe
from woob.tools.backend import Module

from .browser import SupertoinetteBrowser


__all__ = ["SupertoinetteModule"]


class SupertoinetteModule(Module, CapRecipe):
    NAME = "supertoinette"
    MAINTAINER = "Julien Veyssier"
    EMAIL = "julien.veyssier@aiur.fr"
    VERSION = "3.7"
    DESCRIPTION = "Super Toinette, la cuisine familiale French recipe website"
    LICENSE = "AGPLv3+"
    BROWSER = SupertoinetteBrowser

    def get_recipe(self, id):
        return self.browser.get_recipe(id)

    def iter_recipes(self, pattern):
        return self.browser.iter_recipes(pattern.encode("utf-8"))

    def fill_recipe(self, recipe, fields):
        if "nb_person" in fields or "instructions" in fields:
            rec = self.get_recipe(recipe.id)
            recipe.picture = rec.picture
            recipe.instructions = rec.instructions
            recipe.ingredients = rec.ingredients
            recipe.comments = rec.comments
            recipe.author = rec.author
            recipe.nb_person = rec.nb_person
            recipe.cooking_time = rec.cooking_time
            recipe.preparation_time = rec.preparation_time

        return recipe

    OBJECTS = {
        Recipe: fill_recipe,
    }
