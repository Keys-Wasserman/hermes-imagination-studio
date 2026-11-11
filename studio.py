"""A small client for Imagination Studio's HTTP API (standard library only).

Every render goes through the Studio's own queue (``POST /api/jobs``, ``/api/voice/takes``, ``/api/music/takes``), so
the Studio's memory guard, thermal gate and one-job-at-a-time scheduling apply exactly as they do to its own pages.
This module never loads a model or runs a render script itself.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:7870"
FINISHED = ("done", "failed", "cancelled")
SAFE_NAME = re.compile(r"[^\w.-]+")


class StudioError(Exception):
    """Something the person can act on: the Studio is not running, refused the job, or needs its key."""


class Studio:
    def __init__(self, url: str = DEFAULT_URL, key: str = "", timeout: float = 60.0) -> None:
        url = (url or DEFAULT_URL).strip().rstrip("/")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
            raise StudioError(f"studio_url must be an http(s) address like {DEFAULT_URL}, not {url!r}")
        self.url, self.key, self.timeout = url, (key or "").strip(), timeout

    # ------------------------------------------------------------------------------------------------ transport
    def _headers(self, body: bool) -> dict:
        head = {"Accept": "application/json", "User-Agent": "hermes-imagination-studio"}
        if body:
            head["Content-Type"] = "application/json"
        if self.key:
            head["X-Studio-Key"] = self.key
        return head

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=self._headers(data is not None))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                return json.loads(answer.read() or b"null")
        except urllib.error.HTTPError as error:
            said = ""
            try:
                payload = json.loads(error.read() or b"{}")
                said = payload.get("error") or payload.get("detail") or ""
            except (ValueError, AttributeError):
                pass
            if error.code == 401:
                raise StudioError("the Studio needs its access key: set it in this plugin's settings (studio_key), "
                                  "from the Studio's Settings page or ~/ImaginationStudio/access-key on the Studio's machine") from None
            raise StudioError(f"the Studio said: {said or error.reason} ({error.code})") from None
        except (urllib.error.URLError, OSError) as error:
            raise StudioError(f"the Studio does not answer at {self.url}: start it, or set studio_url in this "
                              f"plugin's settings ({getattr(error, 'reason', error)})") from None

    def download(self, library_path: str, folder: Path, limit_bytes: int) -> Path:
        """A Library file (``/files/<path>``) saved into ``folder``; refuses files over ``limit_bytes``."""

        quoted = urllib.parse.quote(library_path.lstrip("/"), safe="/")
        request = urllib.request.Request(f"{self.url}/files/{quoted}", headers=self._headers(False))
        folder.mkdir(parents=True, exist_ok=True)
        name = SAFE_NAME.sub("-", Path(library_path).name).strip("-.") or "output"
        target = folder / name
        partial = target.with_name(target.name + ".part")
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 120.0)) as answer:
                size = int(answer.headers.get("Content-Length") or 0)
                if size > limit_bytes:
                    raise StudioError(f"{library_path} is {size // 2**20} MB, over max_download_mb")
                got = 0
                with partial.open("wb") as out:
                    while chunk := answer.read(1 << 20):
                        got += len(chunk)
                        if got > limit_bytes:
                            raise StudioError(f"{library_path} is over max_download_mb")
                        out.write(chunk)
            partial.replace(target)
            return target
        except urllib.error.HTTPError as error:
            raise StudioError(f"the Studio could not give {library_path} ({error.code})") from None
        except (urllib.error.URLError, OSError) as error:
            raise StudioError(f"downloading {library_path} failed: {getattr(error, 'reason', error)}") from None
        finally:
            partial.unlink(missing_ok=True)

    # ------------------------------------------------------------------------------------------------ the Studio
    def state(self) -> dict:
        return self.call("GET", "/api/state") or {}

    def job(self, job_id: str) -> dict:
        return self.call("GET", "/api/jobs/" + urllib.parse.quote(str(job_id), safe=""))

    def jobs(self) -> list:
        return self.call("GET", "/api/jobs") or []

    def cancel(self, job_id: str) -> dict:
        return self.call("POST", "/api/jobs/" + urllib.parse.quote(str(job_id), safe="") + "/cancel", {})

    def queue(self, kind: str, params: dict, title: str = "") -> dict:
        return self.call("POST", "/api/jobs", {"kind": kind, "title": title or f"Hermes: {kind}", "params": params})

    def voice(self, body: dict) -> dict:
        return self.call("POST", "/api/voice/takes", body)

    def song(self, body: dict) -> dict:
        return self.call("POST", "/api/music/takes", body)

    def search(self, query: str) -> list:
        return (self.call("GET", "/api/library/search?" + urllib.parse.urlencode({"q": query})) or {}).get("items") or []

    def library(self) -> list:
        return self.call("GET", "/api/library") or []

    def memory(self) -> dict:
        return self.call("GET", "/api/memory") or {}

    def wait(self, job_id: str, seconds: float, interval: float = 3.0, clock=time.monotonic, sleep=time.sleep) -> dict:
        """The job once it has finished, or as it stands after ``seconds``."""

        ends = clock() + max(0.0, float(seconds))
        while True:
            job = self.job(job_id)
            if job.get("status") in FINISHED or clock() >= ends:
                return job
            sleep(min(interval, max(0.1, ends - clock())))


def summary(job: dict) -> dict:
    """The parts of a job an agent needs."""

    out = {k: job.get(k) for k in ("id", "kind", "status", "title", "progress", "outputs", "error", "estimate") if job.get(k) not in (None, "", [])}
    if job.get("started") and job.get("finished"):
        out["seconds_taken"] = round(float(job["finished"]) - float(job["started"]), 1)
    return out
