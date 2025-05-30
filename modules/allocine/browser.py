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

import base64
import hashlib
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from woob.browser.browsers import APIBrowser
from woob.browser.profiles import Android
from woob.capabilities.base import NotAvailable, NotLoaded, find_object
from woob.capabilities.calendar import CATEGORIES, STATUS, TRANSP, BaseCalendarEvent
from woob.capabilities.cinema import Movie, Person
from woob.capabilities.collection import Collection
from woob.capabilities.image import Thumbnail
from woob.capabilities.video import BaseVideo


__all__ = ["AllocineBrowser"]


class AllocineBrowser(APIBrowser):
    PROFILE = Android()

    PARTNER_KEY = "100043982026"
    SECRET_KEY = b"29d185d98c984a359e6e6f26a0474269"

    def __do_request(self, method, params):
        params.append(("sed", time.strftime("%Y%m%d", time.localtime())))
        params.append(
            (
                "sig",
                base64.b64encode(hashlib.sha1(self.SECRET_KEY + urlencode(params).encode("utf-8")).digest()).decode(),
            )
        )

        return self.request(f"http://api.allocine.fr/rest/v3/{method}", params=params)

    def iter_movies(self, pattern):
        params = [("partner", self.PARTNER_KEY), ("q", pattern), ("format", "json"), ("filter", "movie")]

        jres = self.__do_request("search", params)
        if jres is None:
            return
        if "movie" not in jres["feed"]:
            return
        for m in jres["feed"]["movie"]:
            tdesc = ""
            if "title" in m:
                tdesc += "%s" % m["title"]
            if "productionYear" in m:
                tdesc += " ; %s" % m["productionYear"]
            elif "release" in m:
                tdesc += " ; %s" % m["release"]["releaseDate"]
            if "castingShort" in m and "actors" in m["castingShort"]:
                tdesc += " ; %s" % m["castingShort"]["actors"]
            short_description = tdesc.strip("; ")
            thumbnail_url = NotAvailable
            if "poster" in m:
                thumbnail_url = str(m["poster"]["href"])
            movie = Movie(m["code"], str(m["originalTitle"]))
            movie.other_titles = NotLoaded
            movie.release_date = NotLoaded
            movie.duration = NotLoaded
            movie.short_description = short_description
            movie.pitch = NotLoaded
            movie.country = NotLoaded
            movie.note = NotLoaded
            movie.roles = NotLoaded
            movie.all_release_dates = NotLoaded
            movie.thumbnail_url = thumbnail_url
            yield movie

    def iter_persons(self, pattern):
        params = [("partner", self.PARTNER_KEY), ("q", pattern), ("format", "json"), ("filter", "person")]

        jres = self.__do_request("search", params)
        if jres is None:
            return
        if "person" not in jres["feed"]:
            return
        for p in jres["feed"]["person"]:
            thumbnail_url = NotAvailable
            if "picture" in p:
                thumbnail_url = str(p["picture"]["href"])
            person = Person(p["code"], str(p["name"]))
            desc = ""
            if "birthDate" in p:
                desc += "(%s), " % p["birthDate"]
            if "activity" in p:
                for a in p["activity"]:
                    desc += "%s, " % a["$"]
            person.real_name = NotLoaded
            person.birth_place = NotLoaded
            person.birth_date = NotLoaded
            person.death_date = NotLoaded
            person.gender = NotLoaded
            person.nationality = NotLoaded
            person.short_biography = NotLoaded
            person.short_description = desc.strip(", ")
            person.roles = NotLoaded
            person.thumbnail_url = thumbnail_url
            yield person

    def get_movie(self, id):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", id),
            ("profile", "large"),
            ("mediafmt", "mp4-lc"),
            ("filter", "movie"),
            ("striptags", "synopsis,synopsisshort"),
            ("format", "json"),
        ]

        jres = self.__do_request("movie", params)
        if jres is not None:
            if "movie" in jres:
                jres = jres["movie"]
            else:
                return None
        else:
            return None
        title = NotAvailable
        duration = NotAvailable
        release_date = NotAvailable
        pitch = NotAvailable
        country = NotAvailable
        note = NotAvailable
        short_description = NotAvailable
        thumbnail_url = NotAvailable
        other_titles = []
        genres = []
        roles = {}

        if "originalTitle" not in jres:
            return
        title = str(jres["originalTitle"].strip())
        if "poster" in jres:
            thumbnail_url = str(jres["poster"]["href"])
        if "genre" in jres:
            for g in jres["genre"]:
                genres.append(g["$"])
        if "runtime" in jres:
            nbsecs = jres["runtime"]
            duration = nbsecs / 60
        if "release" in jres:
            dstr = str(jres["release"]["releaseDate"])
            tdate = dstr.split("-")
            day = 1
            month = 1
            year = 1901
            if len(tdate) > 2:
                year = int(tdate[0])
                month = int(tdate[1])
                day = int(tdate[2])
            release_date = datetime(year, month, day)
        if "nationality" in jres:
            country = ""
            for c in jres["nationality"]:
                country += "%s, " % c["$"]
            country = country.strip(", ")
        if "synopsis" in jres:
            pitch = str(jres["synopsis"])
        if "statistics" in jres and "userRating" in jres["statistics"]:
            note = "{}/5 ({} votes)".format(jres["statistics"]["userRating"], jres["statistics"]["userReviewCount"])
        if "castMember" in jres:
            for cast in jres["castMember"]:
                if cast["activity"]["$"] not in roles:
                    roles[cast["activity"]["$"]] = []
                person_to_append = ("%s" % cast["person"]["code"], cast["person"]["name"])
                roles[cast["activity"]["$"]].append(person_to_append)

        movie = Movie(id, title)
        movie.other_titles = other_titles
        movie.release_date = release_date
        movie.duration = duration
        movie.genres = genres
        movie.pitch = pitch
        movie.country = country
        movie.note = note
        movie.roles = roles
        movie.short_description = short_description
        movie.all_release_dates = NotLoaded
        movie.thumbnail_url = thumbnail_url
        return movie

    def get_person(self, id):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", id),
            ("profile", "large"),
            ("mediafmt", "mp4-lc"),
            ("filter", "movie"),
            ("striptags", "biography,biographyshort"),
            ("format", "json"),
        ]

        jres = self.__do_request("person", params)
        if jres is not None:
            if "person" in jres:
                jres = jres["person"]
            else:
                return None
        else:
            return None
        name = NotAvailable
        short_biography = NotAvailable
        biography = NotAvailable
        short_description = NotAvailable
        birth_place = NotAvailable
        birth_date = NotAvailable
        death_date = NotAvailable
        real_name = NotAvailable
        gender = NotAvailable
        thumbnail_url = NotAvailable
        roles = {}
        nationality = NotAvailable

        if "name" in jres:
            name = ""
            if "given" in jres["name"]:
                name += jres["name"]["given"]
            if "family" in jres["name"]:
                name += " %s" % jres["name"]["family"]
        if "biographyShort" in jres:
            short_biography = str(jres["biographyShort"])
        if "birthPlace" in jres:
            birth_place = str(jres["birthPlace"])
        if "birthDate" in jres:
            df = jres["birthDate"].split("-")
            birth_date = datetime(int(df[0]), int(df[1]), int(df[2]))
        if "deathDate" in jres:
            df = jres["deathDate"].split("-")
            death_date = datetime(int(df[0]), int(df[1]), int(df[2]))
        if "realName" in jres:
            real_name = str(jres["realName"])
        if "gender" in jres:
            gcode = jres["gender"]
            if gcode == "1":
                gender = "Male"
            else:
                gender = "Female"
        if "picture" in jres:
            thumbnail_url = str(jres["picture"]["href"])
        if "nationality" in jres:
            nationality = ""
            for n in jres["nationality"]:
                nationality += "%s, " % n["$"]
            nationality = nationality.strip(", ")
        if "biography" in jres:
            biography = str(jres["biography"])
        if "participation" in jres:
            for m in jres["participation"]:
                if m["activity"]["$"] not in roles:
                    roles[m["activity"]["$"]] = []
                pyear = "????"
                if "productionYear" in m["movie"]:
                    pyear = m["movie"]["productionYear"]
                movie_to_append = ("%s" % (m["movie"]["code"]), "({}) {}".format(pyear, m["movie"]["originalTitle"]))
                roles[m["activity"]["$"]].append(movie_to_append)

        person = Person(id, name)
        person.real_name = real_name
        person.birth_date = birth_date
        person.death_date = death_date
        person.birth_place = birth_place
        person.gender = gender
        person.nationality = nationality
        person.short_biography = short_biography
        person.biography = biography
        person.short_description = short_description
        person.roles = roles
        person.thumbnail_url = thumbnail_url
        return person

    def iter_movie_persons(self, movie_id, role_filter):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", movie_id),
            ("profile", "large"),
            ("mediafmt", "mp4-lc"),
            ("filter", "movie"),
            ("striptags", "synopsis,synopsisshort"),
            ("format", "json"),
        ]

        jres = self.__do_request("movie", params)
        if jres is not None:
            if "movie" in jres:
                jres = jres["movie"]
            else:
                return
        else:
            return
        if "castMember" in jres:
            for cast in jres["castMember"]:
                if role_filter is None or (
                    role_filter is not None and cast["activity"]["$"].lower().strip() == role_filter.lower().strip()
                ):
                    id = cast["person"]["code"]
                    name = str(cast["person"]["name"])
                    short_description = str(cast["activity"]["$"])
                    if "role" in cast:
                        short_description += ", %s" % cast["role"]
                    thumbnail_url = NotAvailable
                    if "picture" in cast:
                        thumbnail_url = str(cast["picture"]["href"])
                    person = Person(id, name)
                    person.short_description = short_description
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

    def iter_person_movies(self, person_id, role_filter):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", person_id),
            ("profile", "medium"),
            ("filter", "movie"),
            ("format", "json"),
        ]

        jres = self.__do_request("filmography", params)
        if jres is not None:
            if "person" in jres:
                jres = jres["person"]
            else:
                return
        else:
            return
        for m in jres["participation"]:
            if role_filter is None or (
                role_filter is not None and m["activity"]["$"].lower().strip() == role_filter.lower().strip()
            ):
                prod_year = "????"
                if "productionYear" in m["movie"]:
                    prod_year = m["movie"]["productionYear"]
                short_description = "({}) {}".format(prod_year, m["activity"]["$"])
                if "role" in m:
                    short_description += ", %s" % m["role"]
                movie = Movie(m["movie"]["code"], str(m["movie"]["originalTitle"]))
                movie.other_titles = NotLoaded
                movie.release_date = NotLoaded
                movie.duration = NotLoaded
                movie.short_description = short_description
                movie.pitch = NotLoaded
                movie.country = NotLoaded
                movie.note = NotLoaded
                movie.roles = NotLoaded
                movie.all_release_dates = NotLoaded
                movie.thumbnail_url = NotLoaded
                yield movie

    def iter_person_movies_ids(self, person_id):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", person_id),
            ("profile", "medium"),
            ("filter", "movie"),
            ("format", "json"),
        ]

        jres = self.__do_request("filmography", params)
        if jres is not None:
            if "person" in jres:
                jres = jres["person"]
            else:
                return
        else:
            return
        for m in jres["participation"]:
            yield str(m["movie"]["code"])

    def iter_movie_persons_ids(self, movie_id):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", movie_id),
            ("profile", "large"),
            ("mediafmt", "mp4-lc"),
            ("filter", "movie"),
            ("striptags", "synopsis,synopsisshort"),
            ("format", "json"),
        ]

        jres = self.__do_request("movie", params)
        if jres is not None:
            if "movie" in jres:
                jres = jres["movie"]
            else:
                return
        else:
            return
        if "castMember" in jres:
            for cast in jres["castMember"]:
                yield str(cast["person"]["code"])

    def get_movie_releases(self, id, country):
        if country == "fr":
            return self.get_movie(id).release_date

    def get_person_biography(self, id):
        params = [
            ("partner", self.PARTNER_KEY),
            ("code", id),
            ("profile", "large"),
            ("mediafmt", "mp4-lc"),
            ("filter", "movie"),
            ("striptags", "biography,biographyshort"),
            ("format", "json"),
        ]

        jres = self.__do_request("person", params)
        if jres is not None:
            if "person" in jres:
                jres = jres["person"]
            else:
                return None
        else:
            return None

        biography = NotAvailable
        if "biography" in jres:
            biography = str(jres["biography"])

        return biography

    def get_categories_movies(self, category):
        params = [("partner", self.PARTNER_KEY), ("format", "json"), ("mediafmt", "mp4"), ("filter", category)]
        result = self.__do_request("movielist", params)
        if result is None:
            return
        for movie in result["feed"]["movie"]:
            if "trailer" not in movie or "productionYear" not in movie:
                continue
            yield self.parse_movie(movie)

    def get_categories_videos(self, category):
        params = [("partner", self.PARTNER_KEY), ("format", "json"), ("mediafmt", "mp4"), ("filter", category)]
        result = self.__do_request("videolist", params)
        if result is None:
            return
        if "feed" in result and "media" in result["feed"]:
            for episode in result["feed"]["media"]:
                if "title" in episode:
                    yield self.parse_video(episode, category)

    def parse_video(self, _video, category):
        video = BaseVideo("{}#{}".format(_video["code"], category))
        video.title = str(_video["title"])
        video._video_code = str(_video["code"])
        video.ext = "mp4"
        if "runtime" in _video:
            video.duration = timedelta(seconds=int(_video["runtime"]))
        if "description" in _video:
            video.description = str(_video["description"])
        renditions = sorted(
            _video["rendition"], key=lambda x: "bandwidth" in x and x["bandwidth"]["code"], reverse=True
        )
        video.url = str(max(renditions, key=lambda x: "bandwidth" in x)["href"])
        return video

    def parse_movie(self, movie):
        video = BaseVideo("{}#{}".format(movie["code"], "movie"))
        video.title = str(movie["trailer"]["name"])
        video._video_code = str(movie["trailer"]["code"])
        video.ext = "mp4"
        if "poster" in movie:
            video.thumbnail = Thumbnail(movie["poster"]["href"])
            video.thumbnail.url = str(movie["poster"]["href"])
        tdate = movie["release"]["releaseDate"].split("-")
        day = 1
        month = 1
        year = 1901
        if len(tdate) > 2:
            year = int(tdate[0])
            month = int(tdate[1])
            day = int(tdate[2])

        video.date = date(year, month, day)
        if "userRating" in movie["statistics"]:
            video.rating = movie["statistics"]["userRating"]
        elif "pressRating" in movie["statistics"]:
            video.rating = movie["statistics"]["pressRating"] * 2
        video.rating_max = 5
        if "synopsis" in movie:
            video.description = str(movie["synopsis"].replace("<p>", "").replace("</p>", ""))
        elif "synopsisShort" in movie:
            video.description = str(movie["synopsisShort"].replace("<p>", "").replace("</p>", ""))
        if "castingShort" in movie:
            if "directors" in movie["castingShort"]:
                video.author = str(movie["castingShort"]["directors"])
        if "runtime" in movie:
            video.duration = timedelta(seconds=int(movie["runtime"]))
        return video

    def get_movie_from_id(self, _id):
        params = [
            ("partner", self.PARTNER_KEY),
            ("format", "json"),
            ("mediafmt", "mp4"),
            ("filter", "movie"),
            ("code", _id),
        ]
        result = self.__do_request("movie", params)
        if result is None:
            return
        return self.parse_movie(result["movie"])

    def get_video_from_id(self, _id, category):
        return find_object(self.get_categories_videos(category), id=f"{_id}#{category}")

    def get_video_url(self, code):
        params = [
            ("partner", self.PARTNER_KEY),
            ("format", "json"),
            ("mediafmt", "mp4"),
            ("code", code),
            ("profile", "large"),
        ]
        result = self.__do_request("media", params)
        if result is None:
            return
        renditions = sorted(
            result["media"]["rendition"], key=lambda x: "bandwidth" in x and x["bandwidth"]["code"], reverse=True
        )
        return max(renditions, key=lambda x: "bandwidth" in x)["href"]

    def get_emissions(self, basename):
        params = [
            ("partner", self.PARTNER_KEY),
            ("format", "json"),
            ("filter", "acshow"),
        ]
        result = self.__do_request("termlist", params)
        if result is None:
            return
        for emission in result["feed"]["term"]:
            yield Collection([basename, str(emission["nameShort"])], str(emission["$"]))

    def search_events(self, query):
        params = [("partner", self.PARTNER_KEY), ("format", "json"), ("zip", query.city)]

        if query.summary:
            movie = next(self.iter_movies(query.summary))
            params.append(("movie", movie.id))

        result = self.__do_request("showtimelist", params)
        if result is None:
            return

        for event in self.create_event(result):
            if (not query.end_date or event.start_date <= query.end_date) and event.start_date >= query.start_date:
                yield event

    def get_event(self, _id):
        split_id = _id.split("#")
        params = [
            ("partner", self.PARTNER_KEY),
            ("format", "json"),
            ("theaters", split_id[0]),
            ("zip", split_id[1]),
            ("movie", split_id[2]),
        ]

        result = self.__do_request("showtimelist", params)
        if result is None:
            return

        for event in self.create_event(result):
            if event.id.split("#")[-1] == split_id[-1]:
                event.description = self.get_movie(split_id[2]).pitch
                return event

    def create_event(self, data):
        sequence = 1
        transp = TRANSP.TRANSPARENT
        status = STATUS.CONFIRMED
        category = CATEGORIES.CINE

        if "theaterShowtimes" not in data["feed"]:
            return

        for items in data["feed"]["theaterShowtimes"]:
            cinema = items["place"]["theater"]
            city = str(cinema["city"])
            location = "{}, {}".format(cinema["name"], cinema["address"])
            postalCode = cinema["postalCode"]
            cinemaCode = cinema["code"]
            for show in items["movieShowtimes"]:
                summary = str(show["onShow"]["movie"]["title"])
                movieCode = show["onShow"]["movie"]["code"]
                for jour in show["scr"]:
                    tdate = jour["d"].split("-")
                    year = int(tdate[0])
                    month = int(tdate[1])
                    day = int(tdate[2])
                    for seance in jour["t"]:
                        ttime = seance["$"].split(":")
                        heure = int(ttime[0])
                        minute = int(ttime[1])
                        start_date = datetime(year=year, month=month, day=day, hour=heure, minute=minute)

                        seanceCode = seance["code"]
                        _id = f"{cinemaCode}#{postalCode}#{movieCode}#{seanceCode}"
                        event = BaseCalendarEvent()
                        event.id = _id
                        event.sequence = sequence
                        event.transp = transp
                        event.status = status
                        event.category = category
                        event.city = city
                        event.location = location
                        event.start_date = start_date
                        event.summary = summary
                        event.timezone = "Europe/Paris"
                        yield event
