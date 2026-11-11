"""The tools Hermes calls: schemas (what the model reads) and handlers (what runs).

Every handler returns a JSON string and never raises. A finished job's files are copied from the Studio into this
plugin's data folder and named in ``media_tag`` as ``MEDIA:<path>`` lines, which Hermes delivers to the chat
(Telegram, Discord, Desktop, ...). A job that is still rendering when the wait runs out comes back with its id, so the
agent can call ``studio_job`` later instead of holding the turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .studio import FINISHED, Studio, StudioError, summary

PLUGIN = "imagination-studio"
WAIT_MAX = 600

# ------------------------------------------------------------------------------------------------------ schemas
_WAIT = {"type": "integer", "minimum": 0, "maximum": WAIT_MAX,
         "description": "seconds to wait for the result before returning the job id (default: the plugin's wait_seconds "
                        "setting, 120). Use 0 to queue and return at once; then call studio_job."}
_SEED = {"type": "integer", "description": "a seed, to make the same result again"}
_TITLE = {"type": "string", "description": "a title for the Studio's queue and Library"}


def _schema(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}


SCHEMAS = {
    "studio_status": _schema(
        "studio_status",
        "Imagination Studio on the person's own machine: what it can make here (picture models, clip sizes and qualities, "
        "voice, song and sound engines and their licences), what is in its queue, and its memory and GPU temperature. "
        "Call it first in a session that will make media.",
        {}),
    "studio_picture": _schema(
        "studio_picture",
        "Make still pictures from a prompt with Imagination Studio (FLUX 3 Klein, Qwen-Image or MOSS-Image, whichever "
        "studio_status lists). Returns the pictures as MEDIA lines once done.",
        {"prompt": {"type": "string", "description": "the picture: subject, place, light, lens, style"},
         "count": {"type": "integer", "minimum": 1, "maximum": 12, "description": "how many pictures (default 1)"},
         "image_model": {"type": "string", "description": "an id from studio_status image_models (default: the Studio's)"},
         "preset": {"type": "string", "description": "a size preset from studio_status (default 1024x576)"},
         "width": {"type": "integer", "description": "with height: an exact size, multiples of 16"},
         "height": {"type": "integer"}, "seed": _SEED, "title": _TITLE, "wait_seconds": _WAIT},
        ("prompt",)),
    "studio_clip": _schema(
        "studio_clip",
        "Make a video clip with sound (MiniMax H3 / FastH3 in Imagination Studio). Give first_frame (a Library path, for "
        "example a picture studio_picture made) to start from it, or image_prompt to make the opening picture first, or "
        "neither for text to video. Clips take minutes: the default wait returns the job id if it is not done yet.",
        {"prompt": {"type": "string", "description": "what happens, the camera and the sound"},
         "first_frame": {"type": "string", "description": "Library path of the opening picture (from a job's outputs)"},
         "image_prompt": {"type": "string", "description": "the opening picture's prompt (picture, then clip, in one job)"},
         "seconds": {"type": "number", "description": "length; the Studio brings it to the nearest it can make (up to 15)"},
         "preset": {"type": "string", "description": "a size preset from studio_status"},
         "quality": {"type": "string", "description": "a quality from studio_status"},
         "seed": _SEED, "title": _TITLE, "wait_seconds": _WAIT},
        ("prompt",)),
    "studio_voice": _schema(
        "studio_voice",
        "Speak a line with Fish Audio Drama 3 in Imagination Studio. Put the voice and delivery in brackets before the "
        "words: \"[a warm older woman, smiling] Welcome home.\" A described voice only: no cloning of real people.",
        {"text": {"type": "string", "description": "the line, with any [direction] in brackets"},
         "seed": _SEED, "title": _TITLE, "wait_seconds": _WAIT},
        ("text",)),
    "studio_song": _schema(
        "studio_song",
        "Make a song with Imagination Studio (MiniMax Music 3; YuE2 where installed): a style caption and lyrics with "
        "section tags ([Verse], [Chorus], [Bridge]). No lyrics makes an instrumental. Write original lyrics: never copy a "
        "real song's words. A three-minute song takes a few minutes.",
        {"caption": {"type": "string", "description": "genre, tempo, mood, vocals and instruments"},
         "lyrics": {"type": "string", "description": "lyrics with section tags, or empty for an instrumental"},
         "seconds": {"type": "number", "minimum": 10, "maximum": 300, "description": "length (default 60)"},
         "engine": {"type": "string", "enum": ["music3", "yue2"], "description": "the song engine (default: the Studio's)"},
         "seed": _SEED, "title": _TITLE, "wait_seconds": _WAIT},
        ("caption",)),
    "studio_sound": _schema(
        "studio_sound",
        "Make a sound effect from words with Imagination Studio (Stable Audio Open): \"heavy rain on a tin roof, distant "
        "thunder\". Returns a .wav.",
        {"prompt": {"type": "string", "description": "the sound, in plain words"},
         "seconds": {"type": "number", "minimum": 1, "maximum": 47, "description": "length (default 6; Windows makes 5)"},
         "seed": _SEED, "title": _TITLE, "wait_seconds": _WAIT},
        ("prompt",)),
    "studio_job": _schema(
        "studio_job",
        "A Studio job's status. Once it is done, its files come back as MEDIA lines. Use it for a job another studio_* "
        "tool returned before it finished.",
        {"job_id": {"type": "string"}, "wait_seconds": _WAIT},
        ("job_id",)),
    "studio_jobs": _schema(
        "studio_jobs",
        "The Studio's queue: what is rendering, waiting, and recently finished.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "at most this many (default 10)"}}),
    "studio_cancel": _schema(
        "studio_cancel",
        "Cancel a queued or running Studio job.",
        {"job_id": {"type": "string"}},
        ("job_id",)),
    "studio_library": _schema(
        "studio_library",
        "Search the Studio's Library by what is in each clip and picture (\"a dog chasing a kite\"), or list the newest. "
        "Returns Library paths to use as first_frame, and can bring a file back as a MEDIA line.",
        {"query": {"type": "string", "description": "what to look for; empty lists the newest"},
         "kind": {"type": "string", "enum": ["image", "video"], "description": "only pictures or only clips"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "at most this many (default 8)"},
         "fetch": {"type": "string", "description": "a Library path to bring back as a MEDIA line instead of searching"}}),
}

TOOL_NAMES = tuple(SCHEMAS)


# ------------------------------------------------------------------------------------------------------ settings
class Settings:
    """The plugin's settings (``plugins.entries.imagination-studio.settings``) and its key, read at each call."""

    def __init__(self, get_config: Callable | None = None, get_secret: Callable | None = None,
                 data_dir: Callable[[], Path] | None = None) -> None:
        self._get_config = get_config or (lambda key, default=None: default)
        self._get_secret = get_secret or (lambda name: "")
        self._data_dir = data_dir

    def get(self, key: str, default):
        try:
            value = self._get_config(key, default)
        except Exception:  # noqa: BLE001 - a broken config never breaks a tool
            return default
        return default if value in (None, "") else value

    def studio(self) -> Studio:
        try:
            key = self._get_secret("IMAGINATION_STUDIO_KEY") or ""
        except Exception:  # noqa: BLE001 - no secret route (a test harness): no key
            key = ""
        return Studio(str(self.get("studio_url", "http://127.0.0.1:7870")), key)

    def wait_seconds(self, args: dict) -> float:
        given = args.get("wait_seconds")
        value = self.get("wait_seconds", 120) if given in (None, "") else given
        try:
            return max(0.0, min(float(value), WAIT_MAX))
        except (TypeError, ValueError):
            return 120.0

    def outputs(self) -> Path:
        if self._data_dir is None:
            raise StudioError("no data folder for downloads")
        return self._data_dir() / "outputs"

    def download_limit(self) -> int:
        try:
            return int(float(self.get("max_download_mb", 500)) * 2**20)
        except (TypeError, ValueError):
            return 500 * 2**20


# ------------------------------------------------------------------------------------------------------ helpers
def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str, **more) -> str:
    return json.dumps({"error": message, **more}, ensure_ascii=False)


def _pick(args: dict, keys: tuple) -> dict:
    return {k: args[k] for k in keys if args.get(k) not in (None, "", [])}


def finish(settings: Settings, studio: Studio, job: dict, wait: float) -> str:
    """Wait for a job, then bring its files back as MEDIA lines (or say how to check on it later)."""

    if job.get("status") not in FINISHED and wait > 0:
        job = studio.wait(str(job["id"]), wait)
    out = summary(job)
    status = job.get("status")
    if status == "done":
        files, skipped = [], []
        if settings.get("download", True) not in (False, "false", "False", 0):
            folder = settings.outputs() / str(job["id"])
            for path in job.get("outputs") or []:
                try:
                    files.append(str(studio.download(str(path), folder, settings.download_limit())))
                except StudioError as error:
                    skipped.append(f"{path}: {error}")
        if files:
            out["files"] = files
            out["media_tag"] = "\n".join(f"MEDIA:{f}" for f in files)
            out["note"] = "Include media_tag's MEDIA lines in your reply to deliver the files."
        if skipped:
            out["not_downloaded"] = skipped
    elif status in ("failed", "cancelled"):
        out["note"] = f"The job {status}." + (f" The Studio said: {job.get('error')}" if job.get("error") else "")
    else:
        out["note"] = (f"Still {status or 'queued'}. Call studio_job with job_id {job.get('id')} later to get the files; "
                       "the Studio paces GPU jobs for memory and temperature, so a wait is normal.")
    return _ok(out)


def _guard(fn: Callable[[Settings, dict], str]) -> Callable:
    """Turns every StudioError (and anything unexpected) into an error result."""

    def handler(settings: Settings, args: dict) -> str:
        try:
            return fn(settings, args or {})
        except StudioError as error:
            return _error(str(error))
        except Exception as error:  # noqa: BLE001 - a tool never raises into the agent loop
            return _error(f"{type(error).__name__}: {error}")

    handler.__name__ = fn.__name__
    return handler


# ------------------------------------------------------------------------------------------------------ handlers
@_guard
def studio_status(settings: Settings, args: dict) -> str:
    studio = settings.studio()
    state = studio.state()
    out = {"studio": studio.url, "version": state.get("version"), "platform": state.get("platform_label") or state.get("platform"),
           "image_models": [{"id": m.get("id"), "label": m.get("label"), "note": m.get("note")} for m in state.get("image_models") or []],
           "presets": list(state.get("presets") or [])[:12], "qualities": list(state.get("qualities") or []),
           "clip_lengths": state.get("lengths") or state.get("durations")}
    for part in ("voice", "music"):
        engine = state.get(part)
        if isinstance(engine, dict):
            out[part] = {k: engine.get(k) for k in ("available", "label", "credit", "licence", "why") if engine.get(k) not in (None, "")}
    try:
        sound = studio.call("GET", "/api/sound/state") or {}
        out["sound"] = {k: {"ready": v.get("ready"), "licence": v.get("licence")} for k, v in (sound.get("kinds") or {}).items()}
    except StudioError:
        pass
    jobs = studio.jobs()
    out["queue"] = [summary(j) for j in jobs if j.get("status") in ("running", "queued")][:10]
    try:
        memory = studio.memory()
        out["memory"] = {k: memory.get(k) for k in ("headroom_mb", "thermal") if k in memory}
    except StudioError:
        pass
    return _ok(out)


@_guard
def studio_picture(settings: Settings, args: dict) -> str:
    if not str(args.get("prompt") or "").strip():
        return _error("prompt is needed")
    params = _pick(args, ("prompt", "count", "preset", "width", "height", "seed", "image_model"))
    params.setdefault("count", 1)
    studio = settings.studio()
    job = studio.queue("scout", params, args.get("title") or "Hermes: picture")
    return finish(settings, studio, job, settings.wait_seconds(args))


@_guard
def studio_clip(settings: Settings, args: dict) -> str:
    if not str(args.get("prompt") or "").strip():
        return _error("prompt is needed")
    studio = settings.studio()
    params = _pick(args, ("prompt", "preset", "quality", "seconds", "seed"))
    if args.get("image_prompt"):
        kind, params["image_prompt"] = "studio", args["image_prompt"]
    else:
        kind = "video"
        if args.get("first_frame"):
            params["image"] = args["first_frame"]
    if "quality" not in params:
        try:
            if not studio.state().get("turbo"):
                params["quality"] = "fh8"  # without the H3 Turbo adapter the Studio's quick route is FastH3
        except StudioError:
            pass
    job = studio.queue(kind, params, args.get("title") or "Hermes: clip")
    return finish(settings, studio, job, settings.wait_seconds(args))


@_guard
def studio_voice(settings: Settings, args: dict) -> str:
    if not str(args.get("text") or "").strip():
        return _error("text is needed")
    studio = settings.studio()
    job = studio.voice(_pick(args, ("text", "seed", "title")))
    return finish(settings, studio, job, settings.wait_seconds(args))


@_guard
def studio_song(settings: Settings, args: dict) -> str:
    if not str(args.get("caption") or "").strip():
        return _error("caption is needed")
    body = _pick(args, ("caption", "seconds", "seed", "title", "engine"))
    body["lyrics"] = str(args.get("lyrics") or "").strip() or "[Instrumental]"
    body.setdefault("seconds", 60)
    studio = settings.studio()
    job = studio.song(body)
    return finish(settings, studio, job, settings.wait_seconds(args))


@_guard
def studio_sound(settings: Settings, args: dict) -> str:
    if not str(args.get("prompt") or "").strip():
        return _error("prompt is needed")
    studio = settings.studio()
    job = studio.queue("sfx", _pick(args, ("prompt", "seconds", "seed")), args.get("title") or "Hermes: sound")
    return finish(settings, studio, job, settings.wait_seconds(args))


@_guard
def studio_job(settings: Settings, args: dict) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return _error("job_id is needed")
    studio = settings.studio()
    wait = settings.wait_seconds({"wait_seconds": args.get("wait_seconds", 0)})
    return finish(settings, studio, studio.job(job_id), wait)


@_guard
def studio_jobs(settings: Settings, args: dict) -> str:
    limit = max(1, min(int(args.get("limit") or 10), 50))
    jobs = settings.studio().jobs()
    return _ok({"jobs": [summary(j) for j in jobs[:limit]]})


@_guard
def studio_cancel(settings: Settings, args: dict) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return _error("job_id is needed")
    return _ok(summary(settings.studio().cancel(job_id) or {"id": job_id, "status": "cancelled"}))


@_guard
def studio_library(settings: Settings, args: dict) -> str:
    studio = settings.studio()
    if args.get("fetch"):
        path = str(args["fetch"])
        file = studio.download(path, settings.outputs() / "library", settings.download_limit())
        return _ok({"path": path, "files": [str(file)], "media_tag": f"MEDIA:{file}",
                    "note": "Include media_tag's MEDIA line in your reply to deliver the file."})
    limit = max(1, min(int(args.get("limit") or 8), 30))
    query = str(args.get("query") or "").strip()
    items = studio.search(query) if query else studio.library()
    if args.get("kind"):
        items = [i for i in items if i.get("kind") == args["kind"]]
    keep = ("path", "kind", "caption", "tags", "score", "prompt")
    return _ok({"items": [{k: i.get(k) for k in keep if i.get(k) not in (None, "", [])} for i in items[:limit]]})


HANDLERS = {"studio_status": studio_status, "studio_picture": studio_picture, "studio_clip": studio_clip,
            "studio_voice": studio_voice, "studio_song": studio_song, "studio_sound": studio_sound,
            "studio_job": studio_job, "studio_jobs": studio_jobs, "studio_cancel": studio_cancel,
            "studio_library": studio_library}
