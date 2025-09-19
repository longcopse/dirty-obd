# web/api.py
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

def build_app(get_latest_sample, get_dtcs, dashboard_html_path: str):
    app = FastAPI()

    @app.get("/")
    async def root():
        with open(dashboard_html_path, "r") as f:
            return HTMLResponse(f.read())

    @app.get("/api/live")
    async def live():
        s = get_latest_sample()
        return s or {}

    @app.get("/api/dtc")
    async def dtc():
        # returns {"stored":[...], "pending":[...], "permanent":[...]}
        return await get_dtcs()

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        try:
            last = None
            while True:
                await asyncio.sleep(0.25)
                s = get_latest_sample()
                if s and s != last:
                    await ws.send_json(s)
                    last = s
        except Exception:
            pass

    return app
