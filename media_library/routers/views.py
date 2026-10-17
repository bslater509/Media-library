"""HTML Page Routes for Media Library and Sorter."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from media_library import __version__
from media_library.core.config import STATIC_DIR
from media_library.core.templating import load_template, render

router = APIRouter()


async def _serve_page(name: str, loading_message: str) -> HTMLResponse:
    """Serve a cached page template with the app version token substituted."""
    template = load_template(name)
    if template is None:
        return HTMLResponse(f"<h1>{loading_message}</h1>", status_code=200)
    return HTMLResponse(content=render(template, VERSION=__version__))


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_library():
    return await _serve_page("library.html", "Media Library page loading...")


@router.api_route("/sorter", methods=["GET", "HEAD"], response_class=HTMLResponse)
@router.api_route("/sort", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_sorter():
    return await _serve_page("index.html", "Video Sorter UI loading...")


@router.api_route("/downloads", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_downloads():
    return await _serve_page("downloads.html", "Downloads page loading...")


@router.api_route("/analytics", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_analytics():
    return await _serve_page("analytics.html", "Analytics page loading...")


@router.api_route("/people", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_people():
    return await _serve_page("people.html", "People page loading...")


@router.api_route("/library", methods=["GET", "HEAD"])
async def redirect_library(request: Request):
    # Preserve the query string (e.g. ?folder=...) so deep links and refreshes
    # reopen the library at the folder the user was browsing.
    qs = request.url.query
    target = "/" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=302)


@router.api_route("/sw.js", methods=["GET", "HEAD"])
async def serve_service_worker():
    sw_file = STATIC_DIR / "sw.js"
    if not sw_file.exists():
        raise HTTPException(status_code=404, detail="Service worker not found")
    return FileResponse(
        str(sw_file),
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )
