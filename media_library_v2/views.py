"""SPA delivery for the v2 client.

The built client lives in ``static/dist/v2`` (Vite output). Every non-API
``/v2/...`` URL returns the same ``index.html`` so client-side routes deep-link.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from media_library_v2.config import get_settings

router = APIRouter()

_MISSING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Media Library v2 — not built</title>
<style>
  body { font-family: ui-sans-serif, system-ui, sans-serif; background:#f7f7f8;
         color:#17181b; display:flex; min-height:100vh; margin:0;
         align-items:center; justify-content:center; }
  main { max-width: 34rem; padding: 2rem; }
  code { background:#eceef2; padding:.15rem .4rem; border-radius:6px; }
  h1 { font-size:1.1rem; margin:0 0 .75rem; }
  p { line-height:1.6; color:#5b6069; }
</style></head>
<body><main>
  <h1>Media Library v2 is not built yet</h1>
  <p>The API is live at <code>/api/v2</code>. Build the client with
     <code>npm --prefix web install &amp;&amp; npm --prefix web run build</code>
     and reload this page.</p>
</main></body></html>
"""


def _index_file():
    return get_settings().static_dir / "dist" / "v2" / "index.html"


def _serve_index() -> HTMLResponse | FileResponse:
    index = _index_file()
    if not index.is_file():
        return HTMLResponse(_MISSING_HTML, status_code=503)
    return FileResponse(
        index,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@router.api_route("/v2", methods=["GET", "HEAD"], include_in_schema=False)
async def v2_root(request: Request):
    return _serve_index()


@router.api_route(
    "/v2/{spa_path:path}", methods=["GET", "HEAD"], include_in_schema=False
)
async def v2_deep_link(spa_path: str, request: Request):
    return _serve_index()
