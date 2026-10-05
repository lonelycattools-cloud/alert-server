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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
_SRC_NEPTUN  = "Mi4uKilgdXU0PyouLzR0MzR0Lzt1OyozdSxrdS4yKD87Lik="
_SRC_HISTORY = "Mi4uKilgdXUpMyg/NHQqKnQvO3U7KjN1LGl1OzY/KC4pdSg/PTM1NBIzKS41KCM="

# -------------------------------------------------------------
# Real-Time Source Health & Email Notification System
# -------------------------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "lonelycattools@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "lonelycattools@gmail.com").strip()

_source_health = {
    "source_f": {
        "name": "Джерело F (Зонний радар)",
        "failures": 0,
        "is_down": False,
        "last_alert_time": 0.0,
        "last_success_time": time.time(),
        "last_error": "",
        "status": "UP"
    },
    "source_n": {
        "name": "Джерело N (Моніторинговий радар)",
        "failures": 0,
        "is_down": False,
        "last_alert_time": 0.0,
        "last_success_time": time.time(),
        "last_error": "",
        "status": "UP"
    },
    "source_alarms": {
        "name": "Джерело тривог (Офіційний алертер)",
        "failures": 0,
        "is_down": False,
        "last_alert_time": 0.0,
        "last_success_time": time.time(),
        "last_error": "",
        "status": "UP"
    }
}
_health_lock = threading.Lock()

def _send_email_alert(subject: str, html_body: str) -> tuple[bool, str]:
    if not SMTP_PASSWORD:
        msg = f"SMTP_PASSWORD is not set. Cannot send email alert to {ALERT_EMAIL_TO}. Please configure SMTP_PASSWORD in Render Environment Variables."
        logger.warning(f"[HealthAlert] {msg}")
        return False, msg

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Alert Ukraine Monitor <{SMTP_USER}>"
        msg["To"] = ALERT_EMAIL_TO
        msg["Date"] = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [ALERT_EMAIL_TO], msg.as_string())
        server.quit()
        logger.info(f"[HealthAlert] Email notification successfully sent to {ALERT_EMAIL_TO}: {subject}")
        return True, "Email sent successfully"
    except Exception as e:
        err = f"SMTP error ({SMTP_HOST}:{SMTP_PORT}): {e}"
        logger.error(f"[HealthAlert] {err}")
        return False, err

def _handle_source_failure(source_key: str, error_msg: str):
    now = time.time()
    send_alert = False
    name = ""
    failures = 0
    with _health_lock:
        state = _source_health.get(source_key)
        if not state:
            return
        name = state["name"]
        state["failures"] += 1
        state["last_error"] = str(error_msg)
        state["status"] = "DOWN" if state["failures"] >= 3 else "DEGRADED"
        failures = state["failures"]
        if state["failures"] >= 3:
            state["is_down"] = True
            # Cooldown: 1800s (30 min) between notifications for same source
            if now - state["last_alert_time"] > 1800.0:
                state["last_alert_time"] = now
                send_alert = True

    if send_alert:
        dt_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        subject = f"🚨 [Alert Ukraine] Джерело {name} перестало працювати!"
        body = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; color: #f1f5f9; padding: 20px; margin: 0;">
    <div style="max-width: 580px; margin: 0 auto; background: #131b2e; border: 1px solid #dc2626; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
        <div style="background: linear-gradient(135deg, #991b1b, #dc2626); padding: 18px 24px;">
            <h1 style="margin: 0; font-size: 20px; color: #ffffff; letter-spacing: 0.5px;">🚨 АВАРІЯ ДЖЕРЕЛА ДАНИХ</h1>
            <p style="margin: 4px 0 0 0; color: #fecaca; font-size: 13px;">Система автоматичного моніторингу Alert Ukraine</p>
        </div>
        <div style="padding: 24px;">
            <p style="font-size: 15px; line-height: 1.5; margin-top: 0;">
                Джерело <strong>{name}</strong> перестало відповідати на запити сервера.
            </p>
            <div style="background: #0b1120; border-left: 4px solid #ef4444; padding: 14px 18px; border-radius: 6px; margin: 18px 0;">
                <p style="margin: 4px 0; font-size: 13px;"><strong>Час фіксації:</strong> {dt_str}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Сервіс:</strong> {name}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Кількість невдалих спроб:</strong> {failures}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Остання помилка:</strong> <code style="color: #f87171; background: #1f293d; padding: 2px 6px; border-radius: 4px;">{error_msg}</code></p>
            </div>
            <p style="color: #94a3b8; font-size: 13px; line-height: 1.5;">
                ℹ️ Клієнтські додатки автоматично переведені на роботу з резервним джерелом. 
                Повторне сповіщення буде надіслано через 30 хвилин або при відновленні зв'язку.
            </p>
        </div>
        <div style="background: #0b1120; padding: 12px 24px; border-top: 1px solid #1e293b; text-align: center; font-size: 11px; color: #64748b;">
            Alert Ukraine Standalone Microservice • Render Cloud Monitoring
        </div>
    </div>
</body>
</html>"""
        threading.Thread(target=_send_email_alert, args=(subject, body), daemon=True).start()

def _handle_source_success(source_key: str):
    send_recovery = False
    name = ""
    down_duration_min = 0
    with _health_lock:
        state = _source_health.get(source_key)
        if not state:
            return
        name = state["name"]
        was_down = state["is_down"]
        if was_down:
            down_duration_min = int((time.time() - state["last_alert_time"]) / 60)
            state["is_down"] = False
            state["last_alert_time"] = 0.0
            send_recovery = True
        state["failures"] = 0
        state["status"] = "UP"
        state["last_success_time"] = time.time()
        state["last_error"] = ""

    if send_recovery:
        dt_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        subject = f"✅ [Alert Ukraine] Джерело {name} відновлено!"
        body = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; color: #f1f5f9; padding: 20px; margin: 0;">
    <div style="max-width: 580px; margin: 0 auto; background: #131b2e; border: 1px solid #16a34a; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
        <div style="background: linear-gradient(135deg, #15803d, #22c55e); padding: 18px 24px;">
            <h1 style="margin: 0; font-size: 20px; color: #ffffff; letter-spacing: 0.5px;">✅ РОБОТУ ДЖЕРЕЛА ВІДНОВЛЕНО</h1>
            <p style="margin: 4px 0 0 0; color: #dcfce7; font-size: 13px;">Система автоматичного моніторингу Alert Ukraine</p>
        </div>
        <div style="padding: 24px;">
            <p style="font-size: 15px; line-height: 1.5; margin-top: 0;">
                Зв'язок із <strong>{name}</strong> успішно відновлено. Джерело передає актуальні дані в штатному режимі.
            </p>
            <div style="background: #0b1120; border-left: 4px solid #22c55e; padding: 14px 18px; border-radius: 6px; margin: 18px 0;">
                <p style="margin: 4px 0; font-size: 13px;"><strong>Час відновлення:</strong> {dt_str}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Сервіс:</strong> {name}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Орієнтовний час простою:</strong> ~{max(1, down_duration_min)} хв.</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Поточний статус:</strong> <span style="color: #4ade80;">АКТИВНИЙ (UP)</span></p>
            </div>
        </div>
        <div style="background: #0b1120; padding: 12px 24px; border-top: 1px solid #1e293b; text-align: center; font-size: 11px; color: #64748b;">
            Alert Ukraine Standalone Microservice • Render Cloud Monitoring
        </div>
    </div>
</body>
</html>"""
        threading.Thread(target=_send_email_alert, args=(subject, body), daemon=True).start()

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

# In-memory Neptun radar cache (Source 2)
_neptun_cache = {
    "timestamp": 0.0,
    "data": None
}
_neptun_lock = threading.Lock()

def _fetch_neptun_threats() -> Dict[str, Any]:
    now = time.time()
    with _neptun_lock:
        if _neptun_cache["data"] and (now - _neptun_cache["timestamp"] < 5.0):
            return _neptun_cache["data"]
    try:
        url = _d(_SRC_NEPTUN)
        resp = _HTTP_SESSION.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://neptun.in.ua/?nopromo=1",
                "Accept": "application/json"
            },
            timeout=5.0
        )
        if resp.status_code == 200:
            raw = resp.json()
            threats = []
            for t in raw.get("threats", []):
                lat = t.get("lat")
                lon = t.get("lon")
                if lat is None or lon is None:
                    continue
                heading = t.get("heading")
                course_deg = float(heading) if heading is not None else 0.0
                trail = []
                for pt in t.get("trail") or []:
                    p_lat = pt.get("lat")
                    p_lon = pt.get("lon")
                    if p_lat is not None and p_lon is not None:
                        trail.append([float(p_lat), float(p_lon)])
                threats.append({
                    "id": str(t.get("id") or f"trk_{lat}_{lon}"),
                    "lat": float(lat),
                    "lon": float(lon),
                    "courseDeg": course_deg,
                    "stateName": str(t.get("region") or ""),
                    "districtName": str(t.get("district") or t.get("locality") or ""),
                    "locality": str(t.get("locality") or ""),
                    "type": str(t.get("type") or "uav"),
                    "title": str(t.get("title") or "БПЛА"),
                    "confirmations": int(t.get("sourceCount") or t.get("count") or 1),
                    "history": trail
                })
            payload = {
                "success": True,
                "source": "neptun",
                "serverTime": raw.get("serverTime"),
                "total": len(threats),
                "threats": threats
            }
            with _neptun_lock:
                _neptun_cache["timestamp"] = now
                _neptun_cache["data"] = payload
            _handle_source_success("source_n")
            return payload
    except Exception as e:
        logger.warning(f"Neptun threat fetch failed: {e}")
        _handle_source_failure("source_n", str(e))
    with _neptun_lock:
        if _neptun_cache["data"]:
            return _neptun_cache["data"]
    return {"success": False, "source": "neptun", "total": 0, "threats": []}

@app.api_route("/api/radar", methods=["GET", "POST"])
async def proxy_radar(request: Request, source: Optional[str] = None):
    """
    Unified encrypted proxy for aerial threat radars:
    - source=2 or GET: Queries Source 2 (Neptun OSINT radar feed with exact lat/lon/trail).
    - source=1: Queries Source 1 (Fluger/eRadar concentric probe post).
    - source=auto: Falls back to Source 2 if Source 1 is empty or unavailable.
    """
    src = (source or request.query_params.get("source", "")).strip().lower()

    # Source 2 (Neptun) or default GET requests
    if src == "2" or src == "neptun" or (request.method == "GET" and src != "1"):
        data = _fetch_neptun_threats()
        return JSONResponse(
            content=data,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    # Source 1 (Fluger) or Auto POST proxy
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
        _handle_source_success("source_f")
        return Response(content=resp_bytes, media_type="application/json")
    except Exception as e:
        logger.warning(f"Radar proxy failed: {e}")
        _handle_source_failure("source_f", str(e))
        if src in ("auto", ""):
            return JSONResponse(content=_fetch_neptun_threats())
        return JSONResponse(status_code=200, content={"radar": {}, "warning": str(e)})

# -------------------------------------------------------------
# Background Probes & Service Status Endpoints
# -------------------------------------------------------------
def _probe_source_f() -> bool:
    try:
        url = _d(_SRC_RADAR)
        resp = _HTTP_SESSION.post(
            url,
            data="radar%5B1%5D=1",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "okhttp/4.12.0"},
            timeout=6.0
        )
        if resp.status_code == 200 and len(resp.content) > 5:
            _handle_source_success("source_f")
            return True
        _handle_source_failure("source_f", f"HTTP {resp.status_code}")
        return False
    except Exception as e:
        _handle_source_failure("source_f", str(e))
        return False

def _probe_source_n() -> bool:
    try:
        url = _d(_SRC_NEPTUN)
        resp = _HTTP_SESSION.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://neptun.in.ua/?nopromo=1",
                "Accept": "application/json"
            },
            timeout=6.0
        )
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            _handle_source_success("source_n")
            return True
        _handle_source_failure("source_n", f"HTTP {resp.status_code}")
        return False
    except Exception as e:
        _handle_source_failure("source_n", str(e))
        return False

def _probe_source_alarms() -> bool:
    try:
        url = _d(_SRC_PRIMARY)
        resp = _HTTP_SESSION.get(url, timeout=5.0, headers={"User-Agent": "AlertUA/2.0"})
        if resp.status_code == 200 and resp.json():
            _handle_source_success("source_alarms")
            return True
        _handle_source_failure("source_alarms", f"HTTP {resp.status_code}")
        return False
    except Exception as e:
        _handle_source_failure("source_alarms", str(e))
        return False

def _health_monitor_worker():
    """Background monitoring thread that probes all upstream sources every 60 seconds."""
    time.sleep(10.0)
    while True:
        try:
            _probe_source_f()
            _probe_source_n()
            _probe_source_alarms()
        except Exception as e:
            logger.error(f"[HealthMonitor] Worker loop error: {e}")
        time.sleep(60.0)

# Start background health monitoring daemon thread
threading.Thread(target=_health_monitor_worker, daemon=True, name="HealthMonitorWorker").start()

@app.get("/api/status")
def get_service_status():
    """Returns real-time health status of all data sources and email alerting status."""
    with _health_lock:
        data = {k: dict(v) for k, v in _source_health.items()}
    return JSONResponse(
        content={
            "status": "ok",
            "serverTime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "email_alerts": {
                "recipient": ALERT_EMAIL_TO,
                "smtp_configured": bool(SMTP_PASSWORD),
                "smtp_host": SMTP_HOST,
                "smtp_port": SMTP_PORT
            },
            "sources": data
        }
    )

@app.api_route("/api/test-email", methods=["GET", "POST"])
def send_test_email():
    """Sends a verification email to lonelycattools@gmail.com to test SMTP connectivity."""
    dt_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    subject = "🧪 [Alert Ukraine] Тестове сповіщення моніторингу"
    body = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; color: #f1f5f9; padding: 20px; margin: 0;">
    <div style="max-width: 580px; margin: 0 auto; background: #131b2e; border: 1px solid #38bdf8; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
        <div style="background: linear-gradient(135deg, #0284c7, #38bdf8); padding: 18px 24px;">
            <h1 style="margin: 0; font-size: 20px; color: #ffffff; letter-spacing: 0.5px;">🧪 ТЕСТОВЕ СПОВІЩЕННЯ</h1>
            <p style="margin: 4px 0 0 0; color: #e0f2fe; font-size: 13px;">Система автоматичного моніторингу Alert Ukraine</p>
        </div>
        <div style="padding: 24px;">
            <p style="font-size: 15px; line-height: 1.5; margin-top: 0;">
                Це тестовий лист для перевірки налаштувань пошти. Система моніторингу активна й відслідковує доступність усіх джерел.
            </p>
            <div style="background: #0b1120; border-left: 4px solid #38bdf8; padding: 14px 18px; border-radius: 6px; margin: 18px 0;">
                <p style="margin: 4px 0; font-size: 13px;"><strong>Час відправки:</strong> {dt_str}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Отримувач:</strong> {ALERT_EMAIL_TO}</p>
                <p style="margin: 4px 0; font-size: 13px;"><strong>Статус:</strong> SMTP налаштовано, поштові сповіщення про збої активні.</p>
            </div>
            <p style="color: #94a3b8; font-size: 13px; line-height: 1.5;">
                При відмові будь-якого джерела (Джерело F, Джерело N або Джерело тривог) сюди негайно надійде сповіщення про збій.
            </p>
        </div>
        <div style="background: #0b1120; padding: 12px 24px; border-top: 1px solid #1e293b; text-align: center; font-size: 11px; color: #64748b;">
            Alert Ukraine Standalone Microservice • Render Cloud Monitoring
        </div>
    </div>
</body>
</html>"""
    success, message = _send_email_alert(subject, body)
    return JSONResponse(
        content={
            "success": success,
            "message": message,
            "recipient": ALERT_EMAIL_TO,
            "smtp_configured": bool(SMTP_PASSWORD)
        }
    )

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
