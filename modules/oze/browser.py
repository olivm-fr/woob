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


from weboob.browser import LoginBrowser, URL, need_login
from weboob.browser.browsers import APIBrowser
from weboob.browser.pages import HTMLPage
from weboob.exceptions import BrowserIncorrectPassword
from weboob.tools.value import Value
from weboob.browser.switch import SiteSwitch
from weboob.capabilities.gauge import *
from weboob.capabilities.base import NotAvailable, NotLoaded, find_object
from weboob.tools.misc import to_unicode

import urllib, json

from .pages import LoginPage, HomePage, GradesPage, CompetenciesPage

__all__ = ['OzeBrowser', 'OzeApiBrowser']

class OzeBrowser(LoginBrowser):
    BASEURL = 'https://ent.colleges-isere.fr'
    
    login_page = URL(r'/my.policy', LoginPage)
    home_page = URL(r'/fr/dashboard', HomePage)
    grades_page = URL(r'/eh/parent/listeNotes.jsp', GradesPage)
    competencies_page = URL(r'/eh/parent/competencesSuivi.jsp', CompetenciesPage)


    def __init__(self, *args, **kwargs):
        LoginBrowser.__init__(self, *args, **kwargs)
 
    def do_login(self):
        if not self.home_page.is_here():
            self.home_page.stay_or_go()

        if self.login_page.is_here():
            self.location('/my.policy', data='private=prive&auth_type_user=autres&vhost=standard&SubmitCreds.x=41&SubmitCreds.y=18')
            self.location('/my.policy', data='username='+urllib.quote_plus(self.username)+'&fakepassword=fake&password='+urllib.quote_plus(self.password)+'&private=public&vhost=standard&SubmitCreds.x=58&SubmitCreds.y=5')

        if self.login_page.is_here():
            raise BrowserIncorrectPassword()
        
    @need_login
    def get_children_list(self):
        raise SiteSwitch('api')

    @need_login
    def get_grade_auth(self, uai):
        raise SiteSwitch('api')

    @need_login
    def get_grade_subjects(self, auth, id):
        if not self.grades_page.is_here():
            etab = json.loads(id.city)
            self.location('/cas/proxySSO/{}?uai={}&projet={}&fonction=TUT'.format(auth, etab['uai'], etab['projet']))
            assert self.grades_page.is_here()
            
        chid = list(self.page.get_children_list())
        self.logger.debug("Searching for %s in %s", id.name, str(chid))
        ch = find_object(chid, name=id.name, error=SensorNotFound)
        if ch.selected == NotAvailable:
            self.logger.debug("requesting child %s", ch.id)
            self.location('/eh/servlet/com.bloobyte.girafe.parent.DoListeNotes', data={'idEleve': ch.id})
            
        self.grades_page.stay_or_go()
        subjects = list(self.page.get_grade_subjects())
        
        self.logger.debug("requesting child %s", ch.id)
        self.location('https://ent.colleges-isere.fr/eh/servlet/com.bloobyte.girafe.parent.DoCompetencesSuivi', data={'idEleve': ch.id})
        self.competencies_page.stay_or_go()
        competencies = list(self.page.get_competencies_subjects())
        
        id.sensors = subjects + competencies
        return id.sensors

    @need_login
    def iter_history(self, sensor, **kwargs):
        self.history.go(idgauge=sensor.gaugeid)
        return self.page.iter_history(sensor=sensor)


class OzeApiBrowser(APIBrowser):
    BASEURL = 'https://api-ent.colleges-isere.fr'
    
    def __init__(self, username, password, *args, **kwargs):
        APIBrowser.__init__(self, *args, **kwargs)

    def get_children_list(self):
        me=self.request(self.BASEURL+'/v1/users/me')
        assert me
        
        etab='Unknown'
        for e in me['etablissements']:
            if e['uai'] == me['currentUai']:
                etab = {'uai' : e['uai'], 'projet': e['projet']}
        
        children = []
        for r in me['relations']:
            if r['estResponsableLegal']:
                c = Gauge()
                c.name = r['user']['nom']# + r['user']['prenom'] + " | " + r['user']['id']
                c.object = "Grades"
                c.city = to_unicode(json.dumps(etab))
                c.sensors = NotLoaded
                children.append(c)
        
        return children
    
    def get_grade_auth(self, uai):
        gradeconf=self.request(self.BASEURL+'/v1/config/EH%20Notes%20Parents?ctx_profil=RESPONSABLE_ELEVE&ctx_etab='+uai)
        return gradeconf['autorisationId']

    def get_grade_subjects(self, auth, id):
        raise SiteSwitch('main')
