# -*- coding: utf-8 -*-

# Copyright(C) 2009-2014  Florent Fourcot
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from __future__ import unicode_literals

from weboob.capabilities.bill import DocumentTypes, Bill, Subscription
from weboob.browser.pages import HTMLPage, LoggedPage, pagination, Form
from weboob.browser.filters.standard import Filter, CleanText, Format, Field, Env, Date
from weboob.browser.filters.html import Attr
from weboob.browser.elements import ListElement, ItemElement, method
from weboob.tools.date import parse_french_date


class FormId(Filter):
    def filter(self, txt):
        formid = txt.split("parameters")[1]
        formid = formid.split("'")[2]
        return formid


class MyForm(Form):
    def submit(self, **kwargs):
        """
        Submit the form but keep current browser.page
        """
        kwargs.setdefault('data_encoding', self.page.encoding)
        return self.page.browser.open(self.request, **kwargs)


class BillsPage(LoggedPage, HTMLPage):
    def build_doc(self, data):
        self.encoding = self.response.encoding
        return super(BillsPage, self).build_doc(data)

    @method
    class iter_subscriptions(ListElement):
        item_xpath = '//ul[@class="unstyled striped"]/li'

        class item(ItemElement):
            klass = Subscription

            obj__javax = Attr("//form[@id='accountsel_form']/input[@name='javax.faces.ViewState']", 'value')
            obj_id = Attr('input', 'value')
            obj_label = CleanText('label')
            obj__formid = FormId(Attr('input', 'onclick'))

    def get_selected_year(self):
        return int(CleanText('//form[@id="years_form"]//ul/li[@class="rich-list-item selected"]')(self.doc))

    def go_to_year(self, year):
        if year == self.get_selected_year():
            return

        ref = Attr('//form[@id="years_form"]//ul//a[text()="%s"]' % year, 'id')(self.doc)

        self.FORM_CLASS = Form
        form = self.get_form(name='years_form')
        form.pop('years_form:j_idcl')
        form.pop('years_form:_link_hidden_')
        form['AJAXREQUEST'] = 'years_form:year_region'
        form[ref] = ref

        return form.submit()

    def download_document(self, bill):
        # MyForm do open, and not location to keep html page as self.page, to reduce number of request on this html page
        self.FORM_CLASS = MyForm
        _id = bill._localid.split("'")[3]

        form = self.get_form(name='downpdf_form')
        form['statements_form'] = 'statements_form'
        form['statements_form:j_idcl'] = _id
        return form.submit()

    @pagination
    @method
    class iter_documents(ListElement):
        flush_at_end = True
        item_xpath = '//ul[@id="statements_form:statementsel"]/li'

        def next_page(self):
            lis = self.page.doc.xpath('//form[@name="years_form"]//li')
            selected = False
            ref = None
            for li in lis:
                if 'rich-list-item selected' in li.attrib['class']:
                    selected = True
                else:
                    if selected:
                        ref = li.find('a').attrib['id']
                        break
            if ref is None:
                return
            form = self.page.get_form(name='years_form')
            form.pop('years_form:j_idcl')
            form.pop('years_form:_link_hidden_')
            form['AJAXREQUEST'] = 'years_form:year_region'
            form[ref] = ref
            return form.request

        def flush(self):
            for obj in reversed(self.objects.values()):
                yield obj

        class item(ItemElement):
            klass = Bill

            obj_label = CleanText('a[1]', replace=[(' ', '-')])
            obj_id = Format('%s-%s', Env('subid'), Field('label'))
            # Force first day of month as label is in form "janvier 2016"
            obj_date = Format('1 %s', Field('label')) & Date(parse_func=parse_french_date)
            obj_format = 'pdf'
            obj_type = DocumentTypes.STATEMENT
            obj__localid = Attr('a[2]', 'onclick')

            def condition(self):
                return (
                    not ('tous les relev' in CleanText('a[1]')(self.el))
                    and not ('annuel' in CleanText('a[1]')(self.el))
                )

            def obj__year(self):
                return int(CleanText('a[1]')(self).split(' ')[1])
