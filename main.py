"""
Alert Ukraine - Standalone High-Performance Microservice.
Dedicated proxy & caching backend for TV Screensaver and Mobile App.
Extremely lightweight (<40MB RAM) to guarantee 100% uptime on Render Free Tier.
Provides real-time parallel multi-source alert aggregation with encrypted upstream origins.
"""

import os
import sys
import time
import json
import base64
import logging
import datetime
import urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List

import requests
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("AlertServer")

app = FastAPI(title="Alert Ukraine Server", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _d(enc_b64: str, key: int = 0x5A) -> str:
    """Decodes XOR-obfuscated upstream endpoints without exposing plain text."""
    raw = base64.b64decode(enc_b64.encode("ascii"))
    return bytes(b ^ key for b in raw).decode("utf-8")

# Encrypted endpoints (obfuscated from automated crawlers and plain searches)
_SRC_PRIMARY = "Mi4uKilgdXUsOz4zNzE2Mzc/NDE1dDk1N3U3Oyp1KS47Li8pPyl0MCk1NA=="
_SRC_BACKUP1 = "Mi4uKilgdXUvODM2NjM0PXQ0Py50Lzt1Oz8oMzs2OzY/KC4pdQ=="
_SRC_BACKUP2 = "Mi4uKilgdXU7Nj8oLil0MzR0Lzt1OyozdSkuOy4/KQ=="
_SRC_RADAR   = "Mi4uKilgdXUoOz47KHQrLzM5MXQvO3U7KjN3aGpobHdqY3dqa3UzNDw1KDc7LjM1NHQqMio="
_SRC_HISTORY = "Mi4uKilgdXUpMyg/NHQqKnQvO3U7KjN1LGl1OzY/KC4pdSg/PTM1NBIzKS41KCM="

_HTTP_SESSION = requests.Session()
_HTTP_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
_HTTP_SESSION.mount("https://", _HTTP_ADAPTER)
_HTTP_SESSION.mount("http://", _HTTP_ADAPTER)

# In-memory alert cache
_alerts_cache = {
    "timestamp": 0.0,
    "data": None
}
_cache_lock = threading.Lock()

def _fetch_source(url_enc: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        url = _d(url_enc)
        resp = _HTTP_SESSION.get(url, timeout=timeout, headers={"User-Agent": "AlertUA/2.0"})
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"Source fetch error: {e}")
    return None

def _refresh_alerts_cache() -> Optional[Dict[str, Any]]:
    """
    Fetches statuses from multiple encrypted upstream providers concurrently.
    Synchronously aggregates them: If ANY provider indicates an active alarm in a region,
    that region is IMMEDIATELY promoted to RED (Zero Latency Alert Injection).
    """
    now = time.time()
    sources = [_SRC_PRIMARY, _SRC_BACKUP1, _SRC_BACKUP2]
    results = []

    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        future_to_src = {executor.submit(_fetch_source, src): src for src in sources}
        for future in as_completed(future_to_src):
            try:
                data = future.result()
                if data:
                    results.append(data)
            except Exception:
                pass

    if not results:
        with _cache_lock:
            return _alerts_cache.get("data")

    # Base structure is preferred from rich primary format (has 'states' with districts)
    primary_data = None
    for r in results:
        if isinstance(r, dict) and "states" in r:
            # Check if has districts
            st = r.get("states", {})
            if any("districts" in v for v in st.values() if isinstance(v, dict)):
                primary_data = r
                break
    if not primary_data and results:
        primary_data = results[0]

    states = dict(primary_data.get("states", {}))
    if not states:
        with _cache_lock:
            return _alerts_cache.get("data")

    # Fast sync: check all secondary sources for any active alarm flags
    alarmed_state_names = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        r_states = r.get("states", {})
        for s_name, s_val in r_states.items():
            if isinstance(s_val, dict):
                if s_val.get("enabled") is True or s_val.get("alertnow") is True or s_val.get("alert_now") is True or s_val.get("alert_level") in ("red", "yellow"):
                    alarmed_state_names.add(s_name.lower().strip())

    regions = []
    red_count = 0
    yellow_count = 0
    rank = {"red": 3, "orange": 2, "yellow": 1, "none": 0}

    for name, s_obj in states.items():
        if not isinstance(s_obj, dict):
            continue
        s_enabled = bool(s_obj.get("enabled", False) or s_obj.get("alertnow", False) or s_obj.get("alert_now", False))
        
        # Zero-latency boost: if ANY source reported an alert for this region, enable immediately!
        if name.lower().strip() in alarmed_state_names:
            s_enabled = True

        s_raw_lvl = s_obj.get("alert_level")
        eff_lvl = ("yellow" if s_raw_lvl == "yellow" else "red") if s_enabled else "none"
        changed = s_obj.get("enabled_at") or s_obj.get("changed") or ""

        # Aggregate district level
        for _, d_obj in (s_obj.get("districts") or {}).items():
            if isinstance(d_obj, dict) and d_obj.get("enabled"):
                d_lvl = "yellow" if d_obj.get("alert_level") == "yellow" else "red"
                if rank.get(d_lvl, 0) > rank.get(eff_lvl, 0):
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

    aggregated_payload = {
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

    with _cache_lock:
        _alerts_cache["timestamp"] = now
        _alerts_cache["data"] = aggregated_payload

    return aggregated_payload

def _start_alerts_background_poller():
    def _loop():
        time.sleep(0.5)
        while True:
            try:
                _refresh_alerts_cache()
            except Exception as e:
                logger.debug(f"Background alert poller error: {e}")
            time.sleep(1.0)
    t = threading.Thread(target=_loop, name="AlertPoller", daemon=True)
    t.start()

# Start background poller on launch
_start_alerts_background_poller()

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Alert Ukraine High-Performance Microservice",
        "version": "1.1.0",
        "endpoints": [
            "/api/alerts",
            "/api/radar",
            "/api/alerts/history",
            "/version.json",
            "/AlertScreensaver.apk",
            "/Alert-Mobile.apk",
            "/AlertScreensaver-Premium.apk",
            "/Alert-Mobile-Premium.apk",
            "/Alert-GooglePlay.apk"
        ]
    }

@app.get("/health")
def health():
    with _cache_lock:
        has_data = _alerts_cache.get("data") is not None
        cached_at = _alerts_cache.get("data", {}).get("cached_at") if has_data else None
    return {
        "status": "ok",
        "has_cached_alerts": has_data,
        "cached_at": cached_at
    }

@app.get("/api/alerts")
def get_alerts() -> Response:
    """Instant in-memory Ukraine air alarm status by oblast and district with zero-cache latency."""
    with _cache_lock:
        data = _alerts_cache.get("data")
    if not data:
        data = _refresh_alerts_cache()
    if not data:
        data = {
            "success": False,
            "error": "Initializing alert mirror...",
            "total_regions": 0,
            "active_alerts_count": 0,
            "regions": []
        }
    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.api_route("/api/radar", methods=["GET", "POST"])
async def proxy_radar(request: Request):
    """Secure proxy for radar drone/missile queries without exposing third-party upstream."""
    body = await request.body()
    try:
        url = _d(_SRC_RADAR)
        resp = _HTTP_SESSION.post(
            url,
            data=body if body else None,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "okhttp/4.12.0"
            },
            timeout=5.0
        )
        resp_bytes = resp.content
        if len(resp_bytes) >= 2 and resp_bytes[:2] == b'\x1f\x8b':
            import gzip
            resp_bytes = gzip.decompress(resp_bytes)
        return Response(content=resp_bytes, media_type="application/json")
    except Exception as e:
        logger.warning(f"Radar proxy failed: {e}")
        return JSONResponse(status_code=200, content={"radar": {}, "warning": str(e)})

@app.get("/api/alerts/history")
def get_alert_history(regionId: str = ""):
    """Proxies region alarm history without exposing origin server."""
    if not regionId:
        return []
    try:
        base_url = _d(_SRC_HISTORY)
        url = f"{base_url}?regionId={urllib.parse.quote(regionId)}"
        resp = _HTTP_SESSION.get(url, timeout=5.0, headers={"User-Agent": "okhttp/4.12.0"})
        return Response(content=resp.content, media_type="application/json")
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
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
        except Exception as e:
            logger.error(f"Error reading version.json: {e}")
    raise HTTPException(status_code=404, detail="version.json not found")

def _serve_apk(filename: str):
    apk_path = os.path.join(BASE_DIR, filename)
    if os.path.isfile(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename=filename,
            headers={
                "Cache-Control": "public, max-age=60",
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    raise HTTPException(status_code=404, detail=f"{filename} not found on server")

@app.api_route("/AlertScreensaver.apk", methods=["GET", "HEAD"])
def download_tv_apk():
    return _serve_apk("AlertScreensaver.apk")

@app.api_route("/Alert-Mobile.apk", methods=["GET", "HEAD"])
def download_mobile_apk():
    return _serve_apk("Alert-Mobile.apk")

@app.api_route("/AlertScreensaver-Premium.apk", methods=["GET", "HEAD"])
def download_tv_premium_apk():
    return _serve_apk("AlertScreensaver-Premium.apk")

@app.api_route("/Alert-Mobile-Premium.apk", methods=["GET", "HEAD"])
def download_mobile_premium_apk():
    return _serve_apk("Alert-Mobile-Premium.apk")

@app.api_route("/Alert-GooglePlay.apk", methods=["GET", "HEAD"])
def download_googleplay_apk():
    return _serve_apk("Alert-GooglePlay.apk")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
