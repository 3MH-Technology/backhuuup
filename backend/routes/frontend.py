from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

router = APIRouter(tags=["Frontend Pages"])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _render_html(filename: str) -> HTMLResponse:
    filepath = TEMPLATES_DIR / filename
    if not filepath.exists():
        return HTMLResponse("<h1>404 Not Found</h1>", status_code=404)
    content = filepath.read_text(encoding="utf-8")
    return HTMLResponse(content)


@router.get("/", response_class=HTMLResponse)
async def index():
    return _render_html("landing.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _render_html("login.html")


@router.get("/register", response_class=HTMLResponse)
async def register_page():
    return _render_html("register.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return _render_html("dashboard.html")


@router.get("/bot/{bot_id}", response_class=HTMLResponse)
async def bot_control_page(bot_id: int):
    return _render_html("bot_control.html")


@router.get("/ai", response_class=HTMLResponse)
async def ai_page():
    return _render_html("ai_assistant.html")


@router.get("/verify", response_class=HTMLResponse)
async def verify_page():
    return _render_html("verify.html")
