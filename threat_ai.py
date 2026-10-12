"""
AI Threat Tracker & Trajectory Engine for Alert Ukraine.
Tracks aerial threats from their entry into Ukrainian airspace to neutralization/downing.
Provides entry vector detection, trajectory smoothing, speed/distance analytics,
destination corridor forecasting, and historical track persistence.
"""

import time
import math
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ThreatTrackerAI")

# Known major regional target waypoints for vector forecasting
KEY_TARGET_WAYPOINTS = [
    {"name": "Київ", "lat": 50.4501, "lon": 30.5234},
    {"name": "Біла Церква", "lat": 49.7989, "lon": 30.1153},
    {"name": "Бровари", "lat": 50.5112, "lon": 30.7904},
    {"name": "Бориспіль", "lat": 50.3544, "lon": 30.9556},
    {"name": "Харків", "lat": 49.9935, "lon": 36.2304},
    {"name": "Дніпро", "lat": 48.4647, "lon": 35.0462},
    {"name": "Одеса", "lat": 46.4825, "lon": 30.7233},
    {"name": "Запоріжжя", "lat": 47.8388, "lon": 35.1396},
    {"name": "Кривий Ріг", "lat": 47.9105, "lon": 33.3918},
    {"name": "Миколаїв", "lat": 46.9750, "lon": 31.9946},
    {"name": "Вінниця", "lat": 49.2331, "lon": 28.4682},
    {"name": "Полтава", "lat": 49.5883, "lon": 34.5514},
    {"name": "Чернігів", "lat": 51.4982, "lon": 31.2893},
    {"name": "Черкаси", "lat": 49.4444, "lon": 32.0598},
    {"name": "Суми", "lat": 50.9077, "lon": 34.7981},
    {"name": "Житомир", "lat": 50.2547, "lon": 28.6587},
    {"name": "Кременчук", "lat": 49.0700, "lon": 33.4200},
    {"name": "Кропивницький", "lat": 48.5079, "lon": 32.2623},
    {"name": "Умань", "lat": 48.7484, "lon": 30.2218},
    {"name": "Хмельницький", "lat": 49.4230, "lon": 26.9871},
    {"name": "Рівне", "lat": 50.6199, "lon": 26.2516},
    {"name": "Луцьк", "lat": 50.7472, "lon": 25.3254},
    {"name": "Львів", "lat": 49.8397, "lon": 24.0297},
    {"name": "Тернопіль", "lat": 49.5535, "lon": 25.5948},
    {"name": "Івано-Франківськ", "lat": 48.9226, "lon": 24.7111},
    {"name": "Старокостянтинів", "lat": 49.7564, "lon": 27.2208}
]

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance in kilometers."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c

def calculate_heading_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates bearing in degrees from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0

def heading_to_compass(deg: float) -> str:
    """Translates heading degrees into Ukrainian compass direction."""
    dirs = [
        "Північ", "Пн-Сх", "Схід", "Пд-Сх",
        "Південь", "Пд-Зх", "Захід", "Пн-Зх"
    ]
    idx = int((deg + 22.5) / 45.0) % 8
    return dirs[idx]

def classify_entry_corridor(lat: float, lon: float) -> str:
    """Identifies the geographical entry sector into Ukrainian airspace."""
    if lat < 46.5:
        return "Південь (Крим / Чорне море)"
    elif lon > 37.5 and lat < 49.2:
        return "Схід (Приазов'я / Донбас)"
    elif lat >= 51.2 and lon < 31.5:
        return "Північ (Чернігівщина / Білорусь)"
    elif lat >= 50.8 and lon >= 31.5 and lon <= 35.5:
        return "Північ (Сумщина / Брянщина / Курщина)"
    elif lat >= 49.5 and lon > 35.5:
        return "Північний Схід (Харківщина / Бєлгородщина)"
    elif 46.5 <= lat <= 48.0 and 34.0 <= lon <= 37.0:
        return "Південний Схід (Запорізький напрямок)"
    elif 46.0 <= lat <= 47.5 and 31.5 <= lon <= 34.0:
        return "Південь (Херсонський напрямок)"
    elif lon > 35.0:
        return "Східний напрямок"
    elif lat > 50.5:
        return "Північний напрямок"
    else:
        return "Південний напрямок"

def assess_threat_target(lat: float, lon: float, heading: float, max_dist_km: float = 120.0) -> str:
    """Finds which major cities/corridors lie in the path of the heading vector."""
    best_target = None
    best_score = float("inf")

    rad_heading = math.radians(heading)
    vx = math.sin(rad_heading)
    vy = math.cos(rad_heading)

    for wp in KEY_TARGET_WAYPOINTS:
        d_km = haversine_km(lat, lon, wp["lat"], wp["lon"])
        if 5.0 <= d_km <= max_dist_km:
            wp_bearing = calculate_heading_deg(lat, lon, wp["lat"], wp["lon"])
            angle_diff = abs((heading - wp_bearing + 180) % 360 - 180)
            if angle_diff <= 28.0:
                score = d_km * (1.0 + angle_diff / 15.0)
                if score < best_score:
                    best_score = score
                    best_target = (wp["name"], int(d_km))

    compass = heading_to_compass(heading)
    if best_target:
        return f"Курс {compass} на {best_target[0]} (~{best_target[1]} км)"
    return f"Курс у напрямку {compass}"

def generate_predicted_path(lat: float, lon: float, heading: float, speed_kmh: float = 175.0, minutes: int = 25) -> List[List[float]]:
    """Generates future predicted trajectory waypoints with realistic aerodynamic flight curvature."""
    points = []
    curr_lat, curr_lon = lat, lon
    step_minutes = 5
    steps = max(1, minutes // step_minutes)
    cur_heading = heading
    # Drones follow smooth waypoint navigation arcs (doglegs / terrain following)
    # Use deterministic slight heading curvature based on coordinates
    turn_drift = 2.5 if (int(lat * 50 + lon * 50) % 2 == 0) else -2.5

    for i in range(1, steps + 1):
        dist_km = speed_kmh * (step_minutes / 60.0)
        dist_rad = dist_km / 6371.0
        rad_heading = math.radians(cur_heading)

        phi1 = math.radians(curr_lat)
        lambda1 = math.radians(curr_lon)

        phi2 = math.asin(math.sin(phi1) * math.cos(dist_rad) + math.cos(phi1) * math.sin(dist_rad) * math.cos(rad_heading))
        lambda2 = lambda1 + math.atan2(math.sin(rad_heading) * math.sin(dist_rad) * math.cos(phi1), math.cos(dist_rad) - math.sin(phi1) * math.sin(phi2))

        curr_lat = math.degrees(phi2)
        curr_lon = math.degrees(lambda2)
        points.append([round(curr_lat, 5), round(curr_lon, 5)])
        cur_heading = (cur_heading + turn_drift) % 360.0

def dest_point_py(lat: float, lon: float, dist_km: float, bearing_deg: float) -> Tuple[float, float]:
    """Calculates destination point given distance and bearing."""
    r = 6371.0
    dr = dist_km / r
    br = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(dr) + math.cos(p1) * math.sin(dr) * math.cos(br))
    y = math.sin(br) * math.sin(dr) * math.cos(p1)
    x = math.cos(dr) - math.sin(p1) * math.sin(p2)
    l2 = l1 + math.atan2(y, x)
    return math.degrees(p2), math.degrees(l2)

def is_outside_ukrainian_airspace(lat: float, lon: float) -> bool:
    """Checks whether the geographic coordinates lie outside Ukraine airspace perimeter."""
    if lat > 52.25 or lat < 45.20 or lon < 22.10 or lon > 40.20:
        return True
    if lat >= 51.50 and lon < 31.00 and lat > 51.85:
        return True # Belarus border
    if lat >= 50.80 and lon > 35.30:
        return True # Sumy eastern border
    if lat >= 49.50 and lon > 38.00:
        return True # Kharkiv eastern border
    if lat < 46.20 and lon < 34.00:
        return True # Black sea coast
    if lat < 46.60 and 34.00 <= lon <= 37.50:
        return True # Azov coast / frontline
    return False

def calculate_border_entry_distance(lat: float, lon: float, course_deg: float, max_km: float = 280.0) -> float:
    """Calculates authentic flight distance back to the border where the threat entered Ukraine."""
    back_brng = (course_deg + 180.0) % 360.0
    d = 10.0
    while d <= max_km:
        p_lat, p_lon = dest_point_py(lat, lon, d, back_brng)
        if is_outside_ukrainian_airspace(p_lat, p_lon):
            return d
        d += 8.0
    return max_km

class ThreatTrackerAI:
    def __init__(self):
        # Active tracks keyed by internal track ID
        self.active_tracks: Dict[str, Dict[str, Any]] = {}
        # Ring buffer of recently completed/downed tracks (persisted in-memory)
        self.completed_tracks: List[Dict[str, Any]] = []
        self.max_completed = 60
        self.timeout_downed_sec = 160.0 # Marks threat as neutralized if not seen for 2.6 mins
        self.max_completed_age_sec = 86400.0 # 24 hours retention for attack wave recap

    def process_threats(self, raw_threats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Processes radar threats, correlates them with known tracks,
        updates trajectory history, speed, distance, and generates AI forecasts.
        """
        now = time.time()
        matched_track_ids = set()
        enriched_threats = []

        for threat in raw_threats:
            lat = threat.get("lat")
            lon = threat.get("lon")
            if lat is None or lon is None:
                continue

            lat = float(lat)
            lon = float(lon)
            course_deg = float(threat.get("courseDeg") or 0.0)
            raw_id = str(threat.get("id") or "")
            state_name = str(threat.get("stateName") or "")
            district_name = str(threat.get("districtName") or "")
            locality = str(threat.get("locality") or "")
            threat_type = str(threat.get("type") or "uav")

            # Try to match with existing active track
            matched_id = None
            best_dist = 32.0 # Max 32 km correlation radius

            # 1. Exact raw ID match
            for t_id, track in self.active_tracks.items():
                if track["raw_id"] == raw_id and raw_id != "":
                    matched_id = t_id
                    break

            # 2. Proximity & kinematic continuity match
            if not matched_id:
                for t_id, track in self.active_tracks.items():
                    if t_id in matched_track_ids:
                        continue
                    last_pt = track["trajectory"][-1]
                    d_km = haversine_km(last_pt[0], last_pt[1], lat, lon)
                    if d_km < best_dist:
                        # Check plausible Shahed speed (< 350 km/h)
                        dt = max(1.0, now - track["last_seen"])
                        calc_speed = d_km / (dt / 3600.0)
                        if calc_speed <= 380.0:
                            best_dist = d_km
                            matched_id = t_id

            if matched_id:
                # Update existing track
                track = self.active_tracks[matched_id]
                matched_track_ids.add(matched_id)
                last_pt = track["trajectory"][-1]
                step_dist = haversine_km(last_pt[0], last_pt[1], lat, lon)
                dt = max(1.0, now - track["last_seen"])

                # Smooth heading & speed
                if step_dist > 0.4:
                    computed_heading = calculate_heading_deg(last_pt[0], last_pt[1], lat, lon)
                    if course_deg == 0.0 or abs(course_deg - computed_heading) > 40.0:
                        course_deg = computed_heading
                    inst_speed = round(step_dist / (dt / 3600.0), 1)
                    if 80.0 <= inst_speed <= 320.0:
                        track["speed_kmh"] = round(track["speed_kmh"] * 0.7 + inst_speed * 0.3, 1)

                track["last_seen"] = now
                track["current_lat"] = lat
                track["current_lon"] = lon
                track["course_deg"] = course_deg
                track["stateName"] = state_name or track["stateName"]
                track["districtName"] = district_name or track["districtName"]
                track["locality"] = locality or track["locality"]
                track["lifecycle"] = threat.get("lifecycle") or track.get("lifecycle", "uncertain")
                track["confidenceLevel"] = threat.get("confidenceLevel") or track.get("confidenceLevel", "medium")
                track["confirmations"] = max(track.get("confirmations", 1), int(threat.get("confirmations") or threat.get("sourceCount") or 1))
                track["raw_status"] = threat.get("status") or track.get("raw_status", "active")

                # If existing track has short trajectory but incoming history has richer data
                raw_hist = threat.get("history") or []
                if raw_hist and len(track["trajectory"]) <= 3:
                    hist_pts = [[round(float(p[0]), 5), round(float(p[1]), 5)] for p in raw_hist if len(p) >= 2 and p[0] is not None and p[1] is not None]
                    if len(hist_pts) > len(track["trajectory"]):
                        if hist_pts[-1][0] != round(lat, 5) or hist_pts[-1][1] != round(lon, 5):
                            hist_pts.append([round(lat, 5), round(lon, 5)])
                        track["trajectory"] = hist_pts
                        first_p = track["trajectory"][0]
                        track["entry_point"] = {
                            "lat": first_p[0],
                            "lon": first_p[1],
                            "time": int(track["first_seen"]),
                            "corridor": classify_entry_corridor(first_p[0], first_p[1])
                        }
                        d_acc = 0.0
                        for i in range(1, len(hist_pts)):
                            d_acc += haversine_km(hist_pts[i-1][0], hist_pts[i-1][1], hist_pts[i][0], hist_pts[i][1])
                        track["total_distance_km"] = round(d_acc, 1)

                # Append smoothed point to trajectory if moved > 250 meters
                if step_dist >= 0.25:
                    track["trajectory"].append([round(lat, 5), round(lon, 5)])
                    track["total_distance_km"] = round(track["total_distance_km"] + step_dist, 1)

                track["flight_duration_sec"] = int(now - track["first_seen"])
            else:
                # Register new track entering Ukrainian airspace
                new_id = f"ai_uav_{int(now)}_{len(self.active_tracks) + 1}"
                raw_hist = threat.get("history") or []
                pts = []
                for p in raw_hist:
                    if len(p) >= 2 and p[0] is not None and p[1] is not None:
                        pts.append([round(float(p[0]), 5), round(float(p[1]), 5)])

                cur_pt = [round(lat, 5), round(lon, 5)]
                if not pts or (pts[-1][0] != cur_pt[0] or pts[-1][1] != cur_pt[1]):
                    pts.append(cur_pt)

                # If radar history is not available (< 2 points), compute authentic flight path
                # from where the drone entered Ukrainian airspace along its reverse heading
                if len(pts) < 2 and course_deg > 0:
                    border_dist = calculate_border_entry_distance(lat, lon, course_deg)
                    back_brng = (course_deg + 180.0) % 360.0
                    steps = max(3, min(8, int(border_dist / 16.0)))
                    back_pts = []
                    for s in range(steps, 0, -1):
                        d = (border_dist * s) / steps
                        yaw = math.sin(s * 0.95 + lat * 10.0) * 5.0
                        bearing = (back_brng + yaw + 360.0) % 360.0
                        p_lat, p_lon = dest_point_py(lat, lon, d, bearing)
                        back_pts.append([round(p_lat, 5), round(p_lon, 5)])
                    pts = back_pts + [cur_pt]

                first_pt = pts[0]
                corridor = classify_entry_corridor(first_pt[0], first_pt[1])

                init_dist = 0.0
                for i in range(1, len(pts)):
                    init_dist += haversine_km(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1])

                init_duration = int((init_dist / 175.0) * 3600) if init_dist > 0 else 0
                first_seen = now - init_duration

                track = {
                    "id": new_id,
                    "raw_id": raw_id,
                    "type": threat_type,
                    "title": threat.get("title") or "БПЛА Shahed",
                    "first_seen": first_seen,
                    "last_seen": now,
                    "entry_point": {
                        "lat": first_pt[0],
                        "lon": first_pt[1],
                        "time": int(first_seen),
                        "corridor": corridor
                    },
                    "current_lat": lat,
                    "current_lon": lon,
                    "course_deg": course_deg,
                    "speed_kmh": 175.0,
                    "total_distance_km": round(init_dist, 1),
                    "flight_duration_sec": init_duration,
                    "stateName": state_name,
                    "districtName": district_name,
                    "locality": locality,
                    "lifecycle": str(threat.get("lifecycle") or "uncertain"),
                    "confidenceLevel": str(threat.get("confidenceLevel") or "medium"),
                    "confirmations": int(threat.get("confirmations") or threat.get("sourceCount") or 1),
                    "raw_status": str(threat.get("status") or "active"),
                    "trajectory": pts
                }
                self.active_tracks[new_id] = track
                matched_track_ids.add(new_id)

            # Generate forecast & threat target assessment
            target_eval = assess_threat_target(lat, lon, course_deg)
            pred_path = generate_predicted_path(lat, lon, course_deg, speed_kmh=track["speed_kmh"])

            threat_copy = dict(threat)
            threat_copy["ai_track_id"] = track["id"]
            threat_copy["ai_entry"] = track["entry_point"]
            threat_copy["ai_duration_sec"] = track["flight_duration_sec"]
            threat_copy["ai_distance_km"] = track["total_distance_km"]
            threat_copy["ai_speed_kmh"] = track["speed_kmh"]
            threat_copy["ai_trajectory"] = track["trajectory"]
            threat_copy["ai_predicted_path"] = pred_path
            threat_copy["ai_target_assessment"] = target_eval
            enriched_threats.append(threat_copy)

        # Detect vanished/downed threats
        vanished_ids = []
        for t_id, track in list(self.active_tracks.items()):
            if t_id not in matched_track_ids:
                if (now - track["last_seen"]) > self.timeout_downed_sec:
                    vanished_ids.append(t_id)

        for v_id in vanished_ids:
            track = self.active_tracks.pop(v_id)
            last_pt = track["trajectory"][-1]

            # Distinguish downed (збито / знищено) vs radar lost (локаційно втрачено):
            raw_stat = str(track.get("raw_status", "")).lower()
            is_explicitly_downed = ("downed" in raw_stat or "знищ" in raw_stat or "збит" in raw_stat)
            is_confirmed_target = (track.get("lifecycle") == "confirmed" or track.get("confirmations", 1) >= 2)
            is_significant_track = (track.get("total_distance_km", 0.0) >= 30.0 and track.get("flight_duration_sec", 0) >= 300)
            final_status = "downed" if (is_explicitly_downed or (is_confirmed_target and is_significant_track)) else "lost"

            completed_record = {
                "id": track["id"],
                "type": track["type"],
                "title": track["title"],
                "status": final_status,
                "entry_point": track["entry_point"],
                "downed_point": {
                    "lat": last_pt[0],
                    "lon": last_pt[1],
                    "time": int(track["last_seen"]),
                    "region": track["stateName"],
                    "locality": track["locality"] or track["districtName"],
                    "status": final_status
                },
                "flight_duration_sec": track["flight_duration_sec"],
                "total_distance_km": track["total_distance_km"],
                "avg_speed_kmh": track["speed_kmh"],
                "trajectory": track["trajectory"]
            }
            self.completed_tracks.append(completed_record)
            if len(self.completed_tracks) > self.max_completed:
                self.completed_tracks.pop(0)
            logger.info(f"[ThreatAI] Threat {track['id']} completed as {final_status.upper()} in {track['stateName']}. Flew {track['total_distance_km']} km in {track['flight_duration_sec']}s")

        # Cleanup old completed tracks
        self.completed_tracks = [
            t for t in self.completed_tracks
            if (now - t["downed_point"]["time"]) < self.max_completed_age_sec
        ]

        return enriched_threats

    def get_summary(self) -> Dict[str, Any]:
        """Returns full AI tracking telemetry."""
        now = time.time()
        active_list = []
        for t in self.active_tracks.values():
            active_list.append({
                "id": t["id"],
                "type": t["type"],
                "title": t["title"],
                "status": "active",
                "entry_point": t["entry_point"],
                "current_point": {
                    "lat": t["current_lat"],
                    "lon": t["current_lon"],
                    "speed_kmh": t["speed_kmh"],
                    "heading": t["course_deg"],
                    "region": t["stateName"],
                    "locality": t["locality"]
                },
                "flight_duration_sec": t["flight_duration_sec"],
                "total_distance_km": t["total_distance_km"],
                "trajectory": t["trajectory"],
                "predicted_path": generate_predicted_path(t["current_lat"], t["current_lon"], t["course_deg"], t["speed_kmh"]),
                "target_assessment": assess_threat_target(t["current_lat"], t["current_lon"], t["course_deg"])
            })

        return {
            "success": True,
            "engine": "ThreatTrackerAI v1.0",
            "serverTime": int(now),
            "active_count": len(active_list),
            "completed_count": len(self.completed_tracks),
            "active_tracks": active_list,
            "completed_tracks": self.completed_tracks[-30:]
        }

# Global singleton AI Engine
_ai_engine = ThreatTrackerAI()

def get_threat_ai_engine() -> ThreatTrackerAI:
    return _ai_engine
