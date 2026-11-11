"""Imagination Studio for Hermes: make pictures, clips, voice lines, songs and sound effects on your own machine.

Registers the studio_* tools, the /studio command and the imagination-studio skill. Everything is sent to the
Imagination Studio at the ``studio_url`` setting (default http://127.0.0.1:7870), which renders it in its own queue.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import tools

logger = logging.getLogger(__name__)

USAGE = ("/studio — Imagination Studio\n"
         "  /studio                 what the Studio can make and what is in its queue\n"
         "  /studio jobs            the queue and recent jobs\n"
         "  /studio picture <prompt>   /studio song <style>   /studio sound <words>   /studio voice <line>\n"
         "                          queue it and return at once (then /studio get <id>)\n"
         "  /studio get <id>        a job's status, and its files once done\n"
         "  /studio cancel <id>     cancel a job\n"
         "Get Imagination Studio (free, open source): https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman")


def _secret(name: str) -> str:
    """The Studio key through Hermes's secret route (catalog rule 11); never from a file."""

    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return ""
    return get_secret(name) or ""


def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(tools.PLUGIN)


def _line(result: str) -> str:
    """A tool result as a short chat reply."""

    data = json.loads(result)
    if data.get("error"):
        return "Studio: " + data["error"]
    if "jobs" in data:
        rows = [f"{j.get('id')}  {j.get('kind')}  {j.get('status')}  {j.get('title') or ''}".rstrip() for j in data["jobs"]]
        return "\n".join(rows) or "The Studio's queue is empty."
    if "version" in data and "queue" in data:
        engines = [m.get("label") for m in data.get("image_models") or []]
        lines = [f"Imagination Studio {data.get('version')} at {data.get('studio')} ({data.get('platform')})",
                 "Pictures: " + (", ".join(e for e in engines if e) or "none")]
        for part in ("voice", "music"):
            if data.get(part):
                lines.append(f"{part.title()}: {'ready' if data[part].get('available') else data[part].get('why') or 'not here'}")
        if data.get("sound"):
            lines.append("Sound: " + ", ".join(k for k, v in data["sound"].items() if v.get("ready")))
        lines.append(f"Queue: {len(data.get('queue') or [])} running or waiting")
        return "\n".join(lines)
    text = f"{data.get('id')}  {data.get('kind')}  {data.get('status')}"
    if data.get("note"):
        text += "\n" + data["note"]
    if data.get("media_tag"):
        text += "\n" + data["media_tag"]
    return text


def register(ctx) -> None:
    settings = tools.Settings(get_config=ctx.get_config, get_secret=_secret, data_dir=_data_dir)

    for name in tools.TOOL_NAMES:
        handler = tools.HANDLERS[name]
        ctx.register_tool(name=name, toolset="imagination_studio", schema=tools.SCHEMAS[name],
                          handler=lambda args, _h=handler, **kwargs: _h(settings, args),
                          description=tools.SCHEMAS[name]["description"], emoji="🎬")

    def command(raw_args: str) -> str:
        words = (raw_args or "").strip().split(None, 1)
        verb, rest = (words[0].lower() if words else ""), (words[1] if len(words) > 1 else "")
        if verb in ("", "status"):
            return _line(tools.studio_status(settings, {}))
        if verb in ("help", "-h", "--help"):
            return USAGE
        if verb == "jobs":
            return _line(tools.studio_jobs(settings, {}))
        if verb in ("get", "job") and rest:
            return _line(tools.studio_job(settings, {"job_id": rest.strip(), "wait_seconds": 0}))
        if verb == "cancel" and rest:
            return _line(tools.studio_cancel(settings, {"job_id": rest.strip()}))
        queue = {"picture": ("studio_picture", "prompt"), "song": ("studio_song", "caption"),
                 "sound": ("studio_sound", "prompt"), "voice": ("studio_voice", "text")}
        if verb in queue and rest.strip():
            name, field = queue[verb]
            return _line(tools.HANDLERS[name](settings, {field: rest.strip(), "wait_seconds": 0}))
        return USAGE

    ctx.register_command("studio", handler=command, description="Imagination Studio: status, queue, make media",
                         args_hint="[jobs|picture|song|sound|voice|get|cancel] ...")

    skills = Path(__file__).parent / "skills"
    for child in sorted(skills.iterdir()) if skills.is_dir() else []:
        if (child / "SKILL.md").is_file():
            ctx.register_skill(child.name, child / "SKILL.md")
