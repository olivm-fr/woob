# Copyright(C) 2011  Julien Hebert
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

from woob.browser.filters.html import XPathNotFound, CSS, CleanHTML
from woob_modules.genericnewspaper.pages import GenericNewsPage


class ArticlePage(GenericNewsPage):
    _selector = CSS

    def on_loaded(self):
        main = self._selector("div#block-article")(self.doc.getroot())
        self.main_div = main[0] if len(main) else None
        self.element_title_selector = "div.header>h1"
        self.element_author_selector = "div.name"
        self.element_body_selector = "div.maincol"

    def get_title(self):
        try:
            return super(self.__class__, self).get_title()
        except(XPathNotFound):
            if self.main_div is None:
                return u""
            else:
                raise

    def get_body(self):
        try:
            element_body = self.get_element_body()
        except XPathNotFound:
            return u'Ceci est un article payant'
        else:
            self.drop_comments(element_body)

            div_header_element = self._selector("div.header")(element_body)[0]
            div_content_element = self._selector("div#the-content")(element_body)[0]

            self.try_remove_from_selector_list(div_header_element,
                                               ["h1", "div.picture", "div.date",
                                                "div.news-single-img", "div.article-top",
                                                "div.metas_img", "strong"])
            self.try_remove_from_selector_list(div_content_element, ["div.tw_button", "div.wpfblike",
                                                                     "blockquote", "p.wp-caption-text", "img"])

            return '%s\n%s' % (CleanHTML('.')(div_header_element), CleanHTML('.')(div_content_element))
