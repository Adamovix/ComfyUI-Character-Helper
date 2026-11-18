"""HTTP endpoints used by the node's widget (web/character_helper.js)."""

from __future__ import annotations

import asyncio

from aiohttp import web
from server import PromptServer

from .tagdb import DB, DEFAULT_RATING, RATINGS

routes = PromptServer.instance.routes


async def _run(fn, *args):
    # database work is CPU bound: keep it off the server's event loop
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def _limit(value, default, top=50):
    try:
        return max(1, min(top, int(value)))
    except (TypeError, ValueError):
        return default


@routes.get("/character_helper/meta")
async def meta(request):
    """What the widget is built from: the fields and the rating choices."""
    db = await _run(DB.load)
    return web.json_response(dict(fields=db.fields, ratings=RATINGS, default_rating=DEFAULT_RATING))


@routes.get("/character_helper/autocomplete")
async def autocomplete(request):
    q = request.query
    return web.json_response(await _run(
        DB.autocomplete, q.get("field", ""), q.get("q", ""), _limit(q.get("limit"), 12)))


@routes.post("/character_helper/suggest")
async def suggest(request):
    body = await request.json()
    return web.json_response(await _run(
        DB.suggest, body.get("field", ""), body.get("context") or {}, _limit(body.get("limit"), 14),
        body.get("rating")))


@routes.get("/character_helper/character")
async def character(request):
    preset = await _run(DB.character_preset, request.query.get("name", ""))
    if preset is None:
        return web.json_response(dict(error="unknown character"), status=404)
    return web.json_response(preset)


@routes.post("/character_helper/random")
async def randomize(request):
    body = await request.json()
    fields = [f for f in body.get("fields") or [] if isinstance(f, str)]
    counts = {f: n for f, n in (body.get("counts") or {}).items() if isinstance(n, (int, float))}
    result = await _run(DB.randomize, fields, counts, body.get("context") or {}, body.get("rating"))
    return web.json_response(dict(fields=result))
