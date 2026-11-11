"""A small client for Imagination Studio's HTTP API (standard library only).

Every render goes through the Studio's own queue (``POST /api/jobs``, ``/api/voice/takes``, ``/api/music/takes``), so
the Studio's memory guard, thermal gate and one-job-at-a-time scheduling apply exactly as they do to its own pages.
This module never loads a model or runs a render script itself.

Safety: the client talks only to ``studio_url``. It never follows a redirect (so the access key cannot be carried to
another address), never sends the key over plain http to a public internet address, accepts only plain relative
Library paths, and writes downloads only inside the folder it is given.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:7870"
STUDIO_REPO = "https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman"
GET_THE_STUDIO = (f"Imagination Studio is free and open source: download it from {STUDIO_REPO} and run its one-shot setup "
                  "for your machine (NVIDIA DGX Spark, RTX PCs, Windows on ARM or Apple Silicon Macs), then start it")
FINISHED = ("done", "failed", "cancelled")
SAFE_NAME = re.compile(r"[^\w.-]+")
CONTROL = re.compile(r"[\x00-\x1f\x7f\x85  ]")


class StudioError(Exception):
    """Something the person can act on: the Studio is not running, refused the job, or needs its key."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None  # a 3xx surfaces as an HTTPError instead of being followed


# no redirects, and no proxies from the environment: requests (and the key) go straight to studio_url
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def safe_name(text: str, fallback: str = "output") -> str:
    """One path component made of letters, digits, '.', '_' and '-' only (never '.', '..' or empty)."""

    name = SAFE_NAME.sub("-", str(text)).strip("-.")[:96]
    return name or fallback


def library_path(path) -> str:
    """A Studio Library path as given by the Studio or the model, checked to be a plain relative path."""

    text = str(path or "").strip()
    if not text or CONTROL.search(text) or "\\" in text or "://" in text or text.startswith(("//", "~")):
        raise StudioError(f"not a Library path: {text[:80]!r}")
    parts = [p for p in text.lstrip("/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise StudioError(f"not a Library path: {text[:80]!r}")
    return "/".join(parts)


def _private_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".ts.net")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10")


class Studio:
    def __init__(self, url: str = DEFAULT_URL, key: str = "", timeout: float = 60.0, key_note: str = "") -> None:
        url = (url or DEFAULT_URL).strip().rstrip("/")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password \
                or parts.query or parts.fragment:
            raise StudioError(f"studio_url must be an http(s) address like {DEFAULT_URL}, not {url!r}")
        key = (key or "").strip()
        if key and parts.scheme == "http" and not _private_host(parts.hostname or ""):
            raise StudioError("the Studio's access key is only sent over https to an internet address: "
                              f"use https:// in studio_url for {parts.hostname}")
        self.url, self.key, self.timeout, self.key_note = url, key, timeout, key_note

    # ------------------------------------------------------------------------------------------------ transport
    def _headers(self, body: bool) -> dict:
        head = {"Accept": "application/json", "User-Agent": "hermes-imagination-studio"}
        if body:
            head["Content-Type"] = "application/json"
        if self.key:
            head["X-Studio-Key"] = self.key
        return head

    def _http_error(self, error: urllib.error.HTTPError, what: str) -> StudioError:
        if 300 <= error.code < 400:
            return StudioError(f"the Studio answered with a redirect ({error.code}); set studio_url to the Studio's "
                               "own address (redirects are not followed, so the access key stays with that address)")
        if error.code == 401:
            hint = f" ({self.key_note})" if self.key_note else ""
            return StudioError("the Studio needs its access key: set it in this plugin's settings (studio_key), "
                               "from the Studio's Settings page or ~/ImaginationStudio/access-key on the Studio's "
                               "machine" + hint)
        said = ""
        try:
            payload = json.loads(error.read(65536) or b"{}")
            said = str(payload.get("error") or payload.get("detail") or "")[:300]
        except (ValueError, AttributeError, OSError):
            pass
        return StudioError(f"{what}: {said or error.reason} ({error.code})")

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=self._headers(data is not None))
        try:
            with _OPENER.open(request, timeout=self.timeout) as answer:
                return json.loads(answer.read(16 * 2**20) or b"null")
        except urllib.error.HTTPError as error:
            raise self._http_error(error, "the Studio said") from None
        except (urllib.error.URLError, OSError) as error:
            raise StudioError(f"Imagination Studio does not answer at {self.url} ({getattr(error, 'reason', error)}). "
                              f"If it is not installed yet: {GET_THE_STUDIO}. If it runs on another machine, set "
                              "studio_url (and studio_key) in this plugin's settings.") from None
        except ValueError:
            raise StudioError(f"the Studio at {self.url} answered with something that is not JSON") from None

    def download(self, path, folder: Path, limit_bytes: int, deadline: float | None = None, clock=time.monotonic) -> Path:
        """A Library file (``/files/<path>``) saved inside ``folder``; refuses files over ``limit_bytes`` and stops
        at ``deadline`` (a ``clock()`` value). Fetching the same Library path again reuses the earlier copy."""

        rel = library_path(path)
        folder.mkdir(parents=True, exist_ok=True)
        base = folder.resolve()
        index_file = base / ".sources.json"
        try:
            index = json.loads(index_file.read_text()) if index_file.is_file() else {}
        except (ValueError, OSError):
            index = {}
        if not isinstance(index, dict):
            index = {}
        known = index.get(rel)
        if isinstance(known, str) and safe_name(known) == known and (base / known).is_file() and not (base / known).is_symlink() and (base / known).parent == base:
            return base / known
        name = safe_name(rel.rsplit("/", 1)[-1])
        target, n = base / name, 1
        while target.exists() or target.is_symlink():  # two different files with one name never overwrite each other
            stem, dot, ext = name.rpartition(".")
            target = base / (f"{stem}-{n}.{ext}" if dot and stem else f"{name}-{n}")
            n += 1
        if target.parent != base:
            raise StudioError(f"refusing to write {rel} outside the plugin's folder")

        def left() -> float:
            if deadline is None:
                return max(self.timeout, 120.0)
            remaining = deadline - clock()
            if remaining <= 0:
                raise StudioError(f"{rel} took too long to download")
            return min(max(self.timeout, 120.0), remaining)

        partial = base / f".{target.name}.{secrets.token_hex(4)}.part"
        made = False
        request = urllib.request.Request(f"{self.url}/files/{urllib.parse.quote(rel, safe='/')}", headers=self._headers(False))
        try:
            with _OPENER.open(request, timeout=left()) as answer:
                try:
                    size = int(answer.headers.get("Content-Length") or 0)
                except ValueError:
                    size = 0
                if size > limit_bytes:
                    raise StudioError(f"{rel} is {size // 2**20} MB, over max_download_mb")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                fd = os.open(partial, flags, 0o644)
                made = True
                sock = getattr(getattr(getattr(answer, "fp", None), "raw", None), "_sock", None)
                got = 0
                with os.fdopen(fd, "wb") as out:
                    while True:
                        wait = left()  # raises once the deadline has passed
                        if answer.isclosed():
                            break
                        if sock is not None:
                            try:
                                sock.settimeout(wait)  # no single read can outlast the deadline
                            except OSError:
                                pass
                        chunk = answer.read1(1 << 16)
                        if not chunk:
                            break
                        got += len(chunk)
                        if got > limit_bytes:
                            raise StudioError(f"{rel} is over max_download_mb")
                        out.write(chunk)
            partial.replace(target)
            made = False
            index[rel] = target.name
            try:
                index_file.write_text(json.dumps(index))
            except OSError:
                pass
            return target
        except urllib.error.HTTPError as error:
            raise self._http_error(error, f"the Studio could not give {rel}") from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            if "timed out" in str(reason):
                raise StudioError(f"{rel} took too long to download") from None
            raise StudioError(f"downloading {rel} failed: {reason}") from None
        finally:
            if made:
                partial.unlink(missing_ok=True)

    # ------------------------------------------------------------------------------------------------ the Studio
    def state(self) -> dict:
        return self.call("GET", "/api/state") or {}

    def job(self, job_id: str) -> dict:
        return self.call("GET", "/api/jobs/" + urllib.parse.quote(str(job_id), safe="")) or {}

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
    try:
        if job.get("started") and job.get("finished"):
            out["seconds_taken"] = round(float(job["finished"]) - float(job["started"]), 1)
    except (TypeError, ValueError):
        pass
    return out
