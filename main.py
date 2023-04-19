import logging
import uvicorn
import asyncio

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from playwright.async_api import async_playwright, Page

from config import settings
import json

ACCESS_TOKEN = None
refersh_access_token_lock = asyncio.Lock()
_logger = logging.getLogger(__name__)


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return all(
            path not in record.getMessage()
            for path in ("/docs", "/openapi.json")
        )


app = FastAPI()
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def exception_handler(request: Request, exc: Exception):
    return PlainTextResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=f"Internal Server Error: {exc}",
    )


async def refersh_access_token(page: Page):
    global ACCESS_TOKEN
    async with refersh_access_token_lock:
        if not settings.auto_refersh_access_token or ACCESS_TOKEN:
            return
        await page.goto(settings.base_url)
        try:
            async with page.expect_response("https://chat.openai.com/api/auth/session") as session:
                value = await session.value
                value_json = await value.json()
                ACCESS_TOKEN = value_json["accessToken"]
                _logger.info("Refreshed access token")
                return
        except Exception as e:
            _logger.exception("Failed to refresh access token", e)
        try:
            await page.locator('//iframe[contains(@src, "cloudflare")]').wait_for(timeout=settings.checkbox_timeout)
            handle = await page.query_selector('//iframe[contains(@src, "cloudflare")]')
            await handle.wait_for_element_state(
                "visible", timeout=settings.checkbox_timeout
            )
            owner_frame = await handle.content_frame()
            await owner_frame.click(
                '//input[@type="checkbox"]',
                timeout=settings.checkbox_timeout
            )
        except Exception as e:
            _logger.exception("Checkbox not found", e)
        try:
            async with page.expect_response("https://chat.openai.com/api/auth/session") as session:
                value = await session.value
                value_json = await value.json()
                ACCESS_TOKEN = value_json["accessToken"]
                _logger.info("Refreshed access token")
                return
        except Exception as e:
            _logger.exception("Failed to refresh access token", e)


@app.get("/admin/refersh_access_token")
async def admin_refersh_access_token():
    global ACCESS_TOKEN
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            settings.browser_server,
            timeout=settings.timeout
        )
        context = browser.contexts[0]
        page = context.pages[0]
        await refersh_access_token(page)
    return {"status": "ok"}


async def _reverse_proxy(request: Request):
    global ACCESS_TOKEN
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            settings.browser_server,
            timeout=settings.timeout
        )
        context = browser.contexts[0]
        page = context.pages[0]
        await refersh_access_token(page)

        access_token = (
            f"Bearer {ACCESS_TOKEN}"
            if ACCESS_TOKEN else
            request.headers.get("Authorization")
        )

        body = await request.body()
        body = (
            "null"
            if request.method.upper() in ("GET", "DELETE")
            else f"{json.dumps(body.decode(), ensure_ascii=False)}"
        )
        target_path = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
        result = await page.evaluate('''
            async () => {
                response = await fetch("https://chat.openai.com%s", {
                    "headers": {
                        "accept": "*/*",
                        "authorization": "%s",
                        "content-type": "application/json",
                    },
                    "referrer": "https://chat.openai.com/",
                    "referrerPolicy": "same-origin",
                    "body": %s,
                    "method": "%s",
                    "mode": "cors",
                    "credentials": "include"
                });
                return {
                    status: response.status,
                    statusText: response.statusText,
                    headers: response.headers,
                    content: await response.text()
                }
            }
            ''' % (target_path, access_token, body, request.method.upper())
        )
        if result["status"] in (401, 403):
            ACCESS_TOKEN = None
            return Response(status_code=result["status"])
        return Response(
            content=result["content"],
            status_code=result["status"],
            headers=result["headers"],
        )


app.add_route(
    "/backend-api/{path:path}",
    _reverse_proxy,
    ["GET", "POST", "DELETE", "PUT", "PATCH"]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
