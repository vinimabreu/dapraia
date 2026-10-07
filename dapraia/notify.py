"""Send the line to your phone through ntfy, the open-source push service.

ntfy needs no account: you subscribe to a topic in the app, and anyone who
knows the topic's name can post to it and read it. So pick a name nobody would
guess. The default server is ntfy.sh; you can run your own and pass its URL.
"""

from __future__ import annotations

import json
import re
import urllib.request

DEFAULT_SERVER = "https://ntfy.sh"
_TOPIC = re.compile(r"^[-_A-Za-z0-9]{1,64}$")


class NotifyError(RuntimeError):
    pass


def _default_opener():
    return urllib.request.build_opener()


def valid_topic(topic: str) -> bool:
    return bool(_TOPIC.match(topic or ""))


def payload(topic: str, title: str, message: str, *, go: bool) -> dict:
    return {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": ["beach_umbrella"] if go else ["cloud"],
        "priority": 3,
    }


def send(topic: str, title: str, message: str, *, go: bool, server: str = DEFAULT_SERVER,
         opener=None) -> None:
    """Publish with ntfy's JSON form, which carries accents in the title without header encoding."""
    if not valid_topic(topic):
        raise NotifyError("an ntfy topic is 1 to 64 letters, digits, - or _")
    body = json.dumps(payload(topic, title, message, go=go), ensure_ascii=False).encode()
    request = urllib.request.Request(server.rstrip("/") + "/", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with (opener or _default_opener()).open(request, timeout=20) as response:
            if response.status >= 300:
                raise NotifyError(f"ntfy answered {response.status}")
    except NotifyError:
        raise
    except Exception as error:
        raise NotifyError(f"could not reach ntfy at {server}: {error}") from error
