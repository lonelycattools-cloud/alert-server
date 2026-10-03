"""
Alert Ukraine - Standalone High-Performance Microservice.
Dedicated proxy & caching backend for TV Screensaver and Mobile App.
Extremely lightweight (<40MB RAM) to guarantee 100% uptime on Render Free Tier.
"""

import os
import sys
import time
import json
import logging
import datetime
import urllib.parse
import urllib.request
import threading
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("AlertServer")

app = FastAPI(title="Alert Ukraine Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# In-memory alert cache
_alerts_cache = {
    "timestamp": 0,
    "data": None
}

def _refresh_alerts_cache():
    """Fetches high-resolution alert statuses with fallbacks and caches them."""
    now = time.time()
    # 1. Primary: Rich statuses with district-level granularity
    try:
        req = urllib.request.Request(
            "https://vadimklimenko.com/map/statuses.json",
            headers={"User-Agent": "AlertAPI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                raw = json.loads(resp.read().decode("utf-8"))
                states = raw.get("states", {})
                if states:
                    regions = []
                    red_count = 0
                    yellow_count = 0
                    rank = {"red": 3, "orange": 2, "yellow": 1, "none": 0}
                    for name, s_obj in states.items():
                        s_enabled = bool(s_obj.get("enabled", False))
                        s_raw_lvl = s_obj.get("alert_level")
                        eff_lvl = ("yellow" if s_raw_lvl == "yellow" else "red") if s_enabled else "none"
                        changed = s_obj.get("enabled_at") or ""
                        for _, d_obj in (s_obj.get("districts") or {}).items():
                            if d_obj.get("enabled"):
                                d_lvl = "yellow" if d_obj.get("alert_level") == "yellow" else "red"
                                if rank[d_lvl] > rank[eff_lvl]:
                                    eff_lvl = d_lvl
                                d_ts = d_obj.get("enabled_at") or ""
                                if d_ts and (not changed or d_ts > changed):
                                    changed = d_ts
                        if eff_lvl == "red":
                            red_count += 1
                        elif eff_lvl == "yellow":
                            yellow_count += 1
                        regions.append({
                            "name": name,
                            "alert_now": eff_lvl in ("red", "yellow"),
                            "alert_level": eff_lvl,
                            "changed": changed
                        })
                    total = len(regions)
                    active_count = red_count + yellow_count
                    safe_count = max(0, total - active_count)
                    percent = round(((red_count + yellow_count * 0.5) / total * 100), 1) if total > 0 else 0.0
                    res = {
                        "success": True,
                        "cached_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "total_regions": total,
                        "active_alerts_count": active_count,
                        "red_alerts_count": red_count,
                        "yellow_alerts_count": yellow_count,
                        "safe_regions_count": safe_count,
                        "percentage": percent,
                        "states": states,
                        "regions": regions
                    }
                    _alerts_cache["timestamp"] = now
                    _alerts_cache["data"] = res
                    return res
    except Exception as e:
        logger.debug(f"Failed to fetch statuses.json: {e}")

    # Retain existing cache if fresh enough (< 120s)
    if _alerts_cache.get("data") and (now - _alerts_cache.get("timestamp", 0) < 120):
        return _alerts_cache["data"]

    # 2. Fallbacks
    urls = [
        "https://ubilling.net.ua/aerialalerts/",
        "https://alerts.in.ua/api/states"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AlertAPI/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    states = raw.get("states", {})
                    if states:
                        regions = []
                        active_count = 0
                        for name, info in states.items():
                            is_alert = bool(info.get("alertnow", False))
                            if is_alert:
                                active_count += 1
                            regions.append({
                                "name": name,
                                "alert_now": is_alert,
                                "alert_level": "red" if is_alert else "none",
                                "changed": info.get("changed", "")
                            })
                        regions.sort(key=lambda x: (not x["alert_now"], x["name"]))
                        total = len(regions)
                        safe_count = total - active_count
                        percent = round((active_count / total * 100), 1) if total > 0 else 0.0
                        res = {
                            "success": True,
                            "cached_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "total_regions": total,
                            "active_alerts_count": active_count,
                            "red_alerts_count": active_count,
                            "yellow_alerts_count": 0,
                            "safe_regions_count": safe_count,
                            "percentage": percent,
                            "states": states,
                            "regions": regions
                        }
                        _alerts_cache["timestamp"] = now
                        _alerts_cache["data"] = res
                        return res
        except Exception as e:
            logger.debug(f"Failed to fetch alerts from {url}: {e}")

    return _alerts_cache.get("data")

def _start_alerts_background_poller():
    def _loop():
        time.sleep(1.0)
        while True:
            try:
                _refresh_alerts_cache()
            except Exception as e:
                logger.debug(f"Background alert poller error: {e}")
            time.sleep(4.0)
    t = threading.Thread(target=_loop, name="AlertPoller", daemon=True)
    t.start()

# Start background poller on launch
_start_alerts_background_poller()

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Alert Ukraine Backend Service",
        "endpoints": [
            "/api/alerts",
            "/api/radar",
            "/api/alerts/history",
            "/version.json",
            "/AlertScreensaver.apk",
            "/Alert-Mobile.apk"
        ]
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "has_cached_alerts": _alerts_cache.get("data") is not None,
        "cached_at": _alerts_cache.get("data", {}).get("cached_at") if _alerts_cache.get("data") else None
    }

@app.get("/api/alerts")
def get_alerts() -> Dict[str, Any]:
    """Instant in-memory Ukraine air alarm status by oblast and district."""
    if _alerts_cache["data"]:
        return _alerts_cache["data"]
    res = _refresh_alerts_cache()
    if res:
        return res
    return {
        "success": False,
        "error": "Initializing alert mirror...",
        "total_regions": 0,
        "active_alerts_count": 0,
        "regions": []
    }

@app.api_route("/api/radar", methods=["GET", "POST"])
async def proxy_radar(request: Request):
    """Secure proxy for radar drone/missile queries without exposing third-party upstream."""
    body = await request.body()
    try:
        req = urllib.request.Request(
            "https://radar.quick.ua/api-2026-09-01/information.php",
            data=body if body else None,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "okhttp/4.12.0"
            }
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            resp_bytes = resp.read()
            if len(resp_bytes) >= 2 and resp_bytes[:2] == b'\x1f\x8b':
                import gzip
                resp_bytes = gzip.decompress(resp_bytes)
            return Response(content=resp_bytes, media_type="application/json")
    except Exception as e:
        logger.warning(f"Radar proxy failed: {e}")
        return JSONResponse(status_code=200, content={"radar": {}, "warning": str(e)})

@app.get("/api/alerts/history")
def get_alert_history(regionId: str = ""):
    """Proxies region alarm history."""
    if not regionId:
        return []
    try:
        req = urllib.request.Request(
            f"https://siren.pp.ua/api/v3/alerts/regionHistory?regionId={urllib.parse.quote(regionId)}",
            headers={"User-Agent": "okhttp/4.12.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            return Response(content=resp.read(), media_type="application/json")
    except Exception as e:
        logger.warning(f"Alert history proxy failed: {e}")
        return []

@app.api_route("/version.json", methods=["GET", "HEAD"])
def get_version_json():
    """Serves the latest version and ads configuration."""
    vfile = os.path.join(BASE_DIR, "version.json")
    if os.path.isfile(vfile):
        try:
            with open(vfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            return JSONResponse(
                content=data,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache"
                }
            )
        except Exception as e:
            logger.error(f"Error reading version.json: {e}")
    raise HTTPException(status_code=404, detail="version.json not found")

@app.api_route("/AlertScreensaver.apk", methods=["GET", "HEAD"])
def download_tv_apk():
    """Direct high-speed download for Standalone TV Screensaver APK."""
    apk_path = os.path.join(BASE_DIR, "AlertScreensaver.apk")
    if os.path.isfile(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="AlertScreensaver.apk"
        )
    raise HTTPException(status_code=404, detail="AlertScreensaver.apk not found on server")

@app.api_route("/Alert-Mobile.apk", methods=["GET", "HEAD"])
def download_mobile_apk():
    """Direct high-speed download for Standalone Mobile App APK."""
    apk_path = os.path.join(BASE_DIR, "Alert-Mobile.apk")
    if os.path.isfile(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="Alert-Mobile.apk"
        )
    raise HTTPException(status_code=404, detail="Alert-Mobile.apk not found on server")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
