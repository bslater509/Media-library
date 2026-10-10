"""HTML Page Routes for Media Library and Sorter."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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


@router.api_route("/library", methods=["GET", "HEAD"])
async def redirect_library(request: Request):
    # Preserve the query string (e.g. ?folder=...) so deep links and refreshes
    # reopen the library at the folder the user was browsing.
    qs = request.url.query
    target = "/" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=302)
