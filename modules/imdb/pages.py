# -*- coding: utf-8 -*-

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

from datetime import datetime
import re

from woob.capabilities.cinema import Person, Movie
from woob.capabilities.base import NotAvailable, NotLoaded
from woob.browser.pages import HTMLPage
from woob.browser.filters.html import CleanHTML


class ReleasePage(HTMLPage):
    ''' Page containing releases of a movie
    '''

    def get_movie_releases(self, country_filter):
        result = ''
        links = self.doc.xpath('//div[@id="releaseinfo_content"]//a')
        for a in links:
            href = a.attrib.get('href', '')

            # XXX: search() could raise an exception
            if href.strip('/').split('/')[0] == 'calendar' and\
                    (country_filter is None or re.search('region=([a-zA-Z]+)', href).group(1).lower() == country_filter):
                country = a.text.strip()
                date = a.xpath('./../../td[has-class("release-date-item__date")]')[0].text
                result += f'{country} : {date}\n'
        if result == u'':
            result = NotAvailable
        else:
            result = result.strip()
        return result


class BiographyPage(HTMLPage):
    ''' Page containing biography of a person
    '''

    def get_biography(self):
        bio = ''
        start = False
        tn = self.doc.xpath('//div[@id="bio_content"]')[0]
        for el in tn.xpath('./*'):
            if el.attrib.get('name') == 'mini_bio':
                start = True

            if start:
                bio += CleanHTML('.')(el)

            content_after_bio = ['family', 'trademark', 'trivia', 'salary']
            if el.attrib.get('name') in content_after_bio:
                break

        return bio


class MovieCrewPage(HTMLPage):
    ''' Page listing all the persons related to a movie
    '''

    def iter_persons(self, role_filter=None):
        if (role_filter is None or (role_filter is not None and role_filter == 'actor')):
            tables = self.doc.xpath('//table[has-class("cast_list")]')
            if len(tables) > 0:
                table = tables[0]
                trs = table.xpath('.//tr')

                for tr in trs:
                    a = tr.xpath('.//a')
                    if len(a) == 3:
                        id = a[1].attrib.get('href', '').strip('/').split('/')[1]
                        name = a[1].text
                        char_name = a[2].text
                        thumbnail_url = a[0].xpath('.//img')[0].attrib.get('src')

                        person = Person(id, name)
                        person.short_description = char_name
                        person.real_name = NotLoaded
                        person.birth_place = NotLoaded
                        person.birth_date = NotLoaded
                        person.death_date = NotLoaded
                        person.gender = NotLoaded
                        person.nationality = NotLoaded
                        person.short_biography = NotLoaded
                        person.roles = NotLoaded
                        person.thumbnail_url = thumbnail_url
                        yield person

        for gloss_link in self.doc.xpath('//table[@cellspacing="1"]//h5//a'):
            role = gloss_link.attrib.get('name', '').rstrip('s')
            if (role_filter is None or (role_filter is not None and role == role_filter)):
                tbody = gloss_link.getparent().getparent().getparent().getparent()
                for line in tbody.xpath('.//tr')[1:]:
                    for a in line.xpath('.//a'):
                        role_detail = NotAvailable
                        href = a.attrib.get('href', '')
                        if '/name/nm' in href:
                            id = href.strip('/').split('/')[-1]
                            name = a.text
                        if 'glossary' in href:
                            role_detail = a.text
                        person = Person(id, name)
                        person.short_description = role_detail
                        yield person
                        # yield self.browser.get_person(id)

    def iter_persons_ids(self):
        tables = self.doc.xpath('//table[has-class("cast_list")]')
        if len(tables) > 0:
            table = tables[0]
            tds = table.xpath('.//td[has-class("character")]')
            for td in tds:
                id = td.find('a').attrib.get('href', '').strip('/').split('/')[1]
                yield id


class PersonPage(HTMLPage):
    ''' Page informing about a person
    It is used to build a Person instance and to get the movie list related to a person
    '''

    def get_person(self, id):
        name = NotAvailable
        short_biography = NotAvailable
        short_description = NotAvailable
        birth_place = NotAvailable
        birth_date = NotAvailable
        death_date = NotAvailable
        real_name = NotAvailable
        gender = NotAvailable
        thumbnail_url = NotAvailable
        roles = {}
        nationality = NotAvailable

        td_overview = self.doc.xpath('//table[@id="name-overview-widget-layout"]')[0]

        names = td_overview.xpath('.//h1//span[@class="itemprop"]')
        if len(names) > 0:
            name = names[0].text.strip()

        descs = td_overview.xpath('.//div[has-class("name-trivia-bio-text")]//div[has-class("inline")]')
        if len(descs) > 0:
            short_biography = CleanHTML('.')(descs[0])

        birth = td_overview.xpath('.//div[@id="name-born-info"]')
        if len(birth) > 0:
            time = birth[0].xpath('.//time')[0].attrib.get('datetime', '').split('-')
            if len(time) == 3 and int(time[0]) >= 1900:
                birth_date = datetime(int(time[0]), int(time[1]), int(time[2]))
            birth_place = birth[0].xpath('.//a')[2].text

        death = td_overview.xpath('.//div[@id="name-death-info"]')
        if len(death) > 0:
            time = death[0].xpath('.//time')[0].attrib.get('datetime', '').split('-')
            if len(time) == 3 and int(time[0]) >= 1900:
                death_date = datetime(int(time[0]), int(time[1]), int(time[2]))

        img_thumbnail = td_overview.xpath('.//td[@id="img_primary"]//img[@id="name-poster"]')
        if len(img_thumbnail) > 0:
            thumbnail_url = img_thumbnail[0].attrib.get('src', '')

        roles = self.get_roles()

        person = Person(id, name)
        person.real_name = real_name
        person.birth_date = birth_date
        person.death_date = death_date
        person.birth_place = birth_place
        person.gender = gender
        person.nationality = nationality
        person.short_biography = short_biography
        person.short_description = short_description
        person.roles = roles
        person.thumbnail_url = thumbnail_url
        return person

    def iter_movies_ids(self):
        for role_div in self.doc.xpath('//div[@id="filmography"]//div[has-class("filmo-category-section")]/div'):
            for a in role_div.xpath('.//a'):
                m = re.search('/title/(tt.*)/\.*', a.attrib.get('href'))
                if m:
                    yield m.group(1)

    def get_roles(self):
        roles = {}
        for role_div in self.doc.xpath('//div[@id="filmography"]/div[has-class("head")]'):
            role = role_div.xpath('.//a')[-1].text
            roles[role] = []
            category = role_div.attrib.get('data-category')
            for infos in self.doc.xpath('//div[@id="filmography"]/div[has-class("filmo-category-section")]/div'):
                if category in infos.attrib.get('id'):
                    roles[role].append(('N/A',infos.text_content().replace('\n', ' ').strip()))
        return roles

    def iter_movies(self, role_filter=None):
        for role_div in self.doc.xpath('//div[@id="filmography"]/div[has-class("filmo-category-section")]/div'):
            for a in role_div.xpath('.//a'):
                m = re.search('/title/(tt.*)/\.*', a.attrib.get('href'))
                if m:
                    yield Movie(m.group(1), a.text)
