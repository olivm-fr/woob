#!/usr/bin/python

from woob.capabilities.collection import CapCollection, Collection
from woob.capabilities.video import BaseVideo, CapVideo

from .base.woobmc2 import Woobmc


class Videoobmc(Woobmc):
    def __init__(self, count=10, nsfw=False):
        Woobmc.__init__(self, count=count)
        self.backends = self.woob.load_backends(CapVideo)
        self.nsfw = nsfw

    def search(self, pattern, backend=""):
        kwargs = {"pattern": pattern, "nsfw": self.nsfw, "backends": backend}

        fields = ["id", "title", "date", "description", "author", "duration", "thumbnail", "url"]
        try:
            yield from self.woob.do(self._do_complete, self.count, fields, "search_videos", **kwargs)
        except Exception as e:
            print(e)

    def get_video(self, video, _backend):
        backend = self.woob.get_backend(_backend)
        fields = ["id", "title", "date", "description", "author", "duration", "thumbnail", "url"]
        return backend.fillobj(video, fields)

    def ls(self, backend, path=""):
        kwargs = {
            "split_path": path.split("/") if path else [],
            "caps": CapCollection,
            "objs": (BaseVideo,),
            "backends": backend,
        }
        fields = []  # ['id', 'title', 'date', 'description', 'author', 'duration', 'thumbnail', 'url']
        result = self.woob.do(self._do_complete, self.count, fields, "iter_resources", **kwargs)
        return self.separate_collections_and_videos(result)

    def separate_collections_and_videos(self, objs):
        videos = []
        categories = []
        for obj in objs:
            if isinstance(obj, Collection):
                categories.append(obj)
            else:
                videos.append(obj)
        return categories, videos

    def download(self, _id, dest, backend):
        for _video in self.woob.do("get_video", _id, backends=backend):
            self.download_obj(_video, dest)
