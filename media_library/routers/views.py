"""HTML Page Routes for Media Library and Sorter."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from media_library.core.config import STATIC_DIR
from media_library.core.templating import load_template

router = APIRouter()


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_library():
    library_template = load_template("library.html")
    if library_template is None:
        return HTMLResponse("<h1>Media Library page loading...</h1>", status_code=200)
    return HTMLResponse(content=library_template)


@router.api_route("/sorter", methods=["GET", "HEAD"], response_class=HTMLResponse)
@router.api_route("/sort", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_sorter():
    sorter_template = load_template("index.html")
    if sorter_template is None:
        return HTMLResponse("<h1>Video Sorter UI loading...</h1>", status_code=200)
    return HTMLResponse(content=sorter_template)


@router.api_route("/downloads", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_downloads():
    downloads_template = load_template("downloads.html")
    if downloads_template is None:
        return HTMLResponse("<h1>Downloads page loading...</h1>", status_code=200)
    return HTMLResponse(content=downloads_template)


@router.api_route("/analytics", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_analytics():
    analytics_template = load_template("analytics.html")
    if analytics_template is None:
        return HTMLResponse("<h1>Analytics page loading...</h1>", status_code=200)
    return HTMLResponse(content=analytics_template)


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
