# flake8: compatible

from __future__ import annotations

import shlex
from typing import TypedDict

import requests
from typing_extensions import NotRequired

from woob.tools.json import json


__all__ = ["to_curl"]


class RequestDict(TypedDict):
    method: str
    url: str
    headers: str  # JSON formatted HTTP headers mapping
    body: NotRequired[str | bytes]


def to_curl(request: requests.PreparedRequest | RequestDict) -> str:
    """Return a generated functional curl command based on a request.

    :param request: The prepared request, or property dictionary, to
        transform into a curl command.
    :return: The curl command, with UNIX newlines.
    """

    if isinstance(request, requests.PreparedRequest):
        assert request.method and request.url
        method = request.method
        url = request.url
        headers: dict[str, str] = dict(request.headers)
        body: str | bytes | None = request.body
    else:
        method = request["method"]
        url = request["url"]
        headers = json.loads(str(request["headers"]).replace("'", '"'))
        body = request.get("body")

    parts = [
        "curl",
        "--compressed",  # Decompress encoded data before stdout
    ]

    if method not in ("GET", "POST"):
        parts += ["-X", method]

    for header, value in headers.items():
        parts += ["-H", f"{header}:{value}"]

    if body:
        if isinstance(body, bytes):
            parts += ["-d", f'{body.decode("utf-8")}']
        else:
            parts += ["-d", f"{body}"]

    parts += [f"{url}"]

    return shlex.join(parts)
