"""HTML Page Routes for Media Library and Sorter."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from media_library.core.config import TEMPLATES_DIR

router = APIRouter()

@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_library():
    library_file = TEMPLATES_DIR / "library.html"
    if not library_file.exists():
        return HTMLResponse("<h1>Media Library page loading...</h1>", status_code=200)
    return HTMLResponse(content=library_file.read_text(encoding="utf-8"))

@router.api_route("/sorter", methods=["GET", "HEAD"], response_class=HTMLResponse)
@router.api_route("/sort", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_sorter():
    sorter_file = TEMPLATES_DIR / "index.html"
    if not sorter_file.exists():
        return HTMLResponse("<h1>Video Sorter UI loading...</h1>", status_code=200)
    return HTMLResponse(content=sorter_file.read_text(encoding="utf-8"))

@router.api_route("/library", methods=["GET", "HEAD"])
async def redirect_library():
    return RedirectResponse(url="/", status_code=302)
