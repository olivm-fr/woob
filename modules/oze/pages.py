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

from weboob.browser.pages import HTMLPage, LoggedPage
from weboob.browser.elements import ListElement, ItemElement, method
from weboob.browser.filters.standard import Env, CleanText, CleanDecimal, Regexp, Field, Date, DateTime, Map
from weboob.browser.filters.html import Attr, XPath
from weboob.capabilities.gauge import Gauge, GaugeMeasure, GaugeSensor
from weboob.capabilities.base import NotAvailable, NotLoaded, StringField

from weboob.exceptions import BrowserUnavailable, ParseError

import re, sys, datetime, unicodedata
try:
    import parsedatetime
except ImportError:
    raise ImportError("Please install python-parsedatetime")

class Children:
    name = StringField("Name of the child")
    id = StringField("Id of the child")
    selected = StringField("Id of the child")
    def __str__(self):
        return '{} ({})'.format(self.name, self.id)
    def __repr__(self):
        return '{} ({}) [{}]'.format(self.name, self.id, self.selected)
    def __unicode__(self):
        return u'{} ({})'.format(self.name, self.id)

class Subject:
    name = StringField("Name of the subject")
    id = StringField("Id of the subject")
    def __str__(self):
        return '{}'.format(self.name)
    def __repr__(self):
        return '{}'.format(self.name)
    def __unicode__(self):
        return u'{}'.format(self.name)
    
class Grade(GaugeMeasure):
    details =     StringField('Details')
    isCompetency = False
    def parse_date(self):
        if self.date is NotAvailable: return ""
        try:
            return " on "+datetime.datetime.strftime(self.date, "%b %d")
        except:
            return ""
    def __str__(self):
        return unicodedata.normalize('NFKD', self.__unicode__()).encode("ascii", errors="ignore")
    def __repr__(self):
        return self.__str__()
    def __unicode__(self):
        return u'{}{} {}{} [{}]'.format(
                                        "C" if self.isCompetency else "", self.level, 
                                         self.alarm if self.isCompetency else ("(avg "+self.alarm+")"), 
                                         self.parse_date(), 
                                         self.details)

    

class LoginPage(HTMLPage):
    pass

class HomePage(HTMLPage):
    pass

class GradesPage(HTMLPage):
    @method
    class get_children_list(ListElement):
        item_xpath = '//form[@name="fNotes"]//select[@name="idEleve"]/option'
        class item(ItemElement):
            klass = Children
            obj_name = Regexp(CleanText('./text()'), r'(.*?) *-.*')
            obj_id = Attr('.', 'value')
            obj_selected = Attr('.', 'selected', default=NotAvailable)
        
    @method
    class get_grade_subjects(ListElement):
        item_xpath = "//table[@class='tableaumatieres']//td"

        class item(ItemElement):
            klass = GaugeSensor
            obj_name = CleanText('.//text()')
            def obj_id(self):
                return CleanText('//form[@name="fNotes"]//select[@name="idEleve"]/option[@selected="selected"]/text()')(self) + ":" + CleanText('.//text()')(self)
        
            def get_all_grades(self):
                self.logger.debug("Getting grades for %s", CleanText('.//text()')(self))
                calendar = parsedatetime.Calendar(parsedatetime.Constants(localeID='fr_FR', usePyICU=False))
                grades = []
                for tr in XPath('//table[contains(@class, "tableaunotes")][tr[1]//a[text()="{}"]]/tr[contains(@id, "ligne")]'.format(CleanText('.//text()')(self)))(self):
                    try:
                        grade = CleanDecimal('./td[4]//text()', replace_dots=True)(tr)
                    except Exception as e:
                        try:
                            grade = CleanDecimal(Regexp(CleanText('./td[4]//text()'), r'.*\( *?([0-9,]+) */ *20 *\).*'), replace_dots=True)(tr)
                        except Exception as e:
                            self.logger.warning("Cannot convert value : " + str(e), sys.exc_info()[0])
                            grade = NotAvailable
                    m = Grade()
                    m.level = grade
                    m.date, _ = calendar.parseDT(Regexp(CleanText('./td[3]//text()'), r' *\w +([^,]+),.*')(tr) + " 00:00:00")
                    m.alarm = Regexp(CleanText('./td[5]//text()'), r' *([0-9,]+).*')(tr)
                    m.id = CleanText('.//text()')(self)+str(m.level)+str(m.date)+m.alarm
                    m.details = CleanText('./td[2]//text()')(tr)
                    grades.append(m)
                return grades
                
            def obj_history(self):   
                return self.get_all_grades()[0:-1]
                
            def obj_lastvalue(self):   
                try:
                    return self.get_all_grades()[-1]
                except:
                    return NotAvailable

class CompetenciesPage(HTMLPage):
    @method
    class get_competencies_subjects(ListElement):
        item_xpath = "//table[@class='tableaumatieres']//td"

        class item(ItemElement):
            klass = GaugeSensor
            obj_name = CleanText('.//text()')
            def obj_id(self):
                return CleanText('//form[@name="fCompetences"]//select[@name="idEleve"]/option[@selected="selected"]/text()')(self) + ":" + CleanText('.//text()')(self)
        
            def get_all_grades(self):
                self.logger.debug("Getting competencies for %s", CleanText('.//text()')(self))
                grades = []
                for tr in XPath('//tr[contains(@class,"masquepc")]//table[contains(@class, "tableaunotes")][tr[1]//a[text()="{}"]]/tr[td[contains(@id, "-")]]'.format(CleanText('.//text()')(self)))(self):
                    for td in XPath('td[contains(@id, "-")]')(tr):
                        m = Grade()
                        m.isCompetency = True
                        m.level = CleanDecimal('text()')(td)
                        m.date = NotAvailable
                        m.alarm = CleanText(Attr('.', 'title'))(td)
                        col = XPath('count(preceding-sibling::td)')(td) + 1
                        tst = Attr('../tr[2]/th[{}]'.format(col), 'title')(tr)
                        m.details = CleanText('../td[2]//text()')(td) + " | " + tst
                        m.id = unicodedata.normalize('NFKD', CleanText('.//text()')(self)+str(m.level) + str(int(col)) + m.details).encode("ascii", errors="ignore")
                        grades.append(m)
                return grades
                
            def obj_history(self):   
                return self.get_all_grades()[0:-1]
                
            def obj_lastvalue(self):   
                try:
                    return self.get_all_grades()[-1]
                except:
                    return NotAvailable
