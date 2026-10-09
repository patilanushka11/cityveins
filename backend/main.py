"""CityVeins API: FastAPI on Cloud Run. Auth: Firebase. DB: Firestore. AI: Gemini."""
import os, time, math, json, subprocess
import httpx, polyline, pygeohash as pgh
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials
from firebase_admin import auth as fb_auth
from google.cloud import firestore
from google import genai
from google.genai import types
from schemas import Report, Insights
from prompts import EXTRACT_SYSTEM, INSIGHT_SYSTEM

service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
if service_account_json:
    firebase_admin.initialize_app(credentials.Certificate(json.loads(service_account_json)))
else:
    firebase_admin.initialize_app()
db = firestore.Client()
ai = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAPS_KEY = os.getenv("MAPS_API_KEY", "")
HAZARDS = {"flood", "traffic", "safety", "civic_issue"}
app = FastAPI(title="CityVeins")

# ---------- auth + abuse control ----------
def user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in required")
    try:
        return fb_auth.verify_id_token(authorization[7:])
    except Exception:
        raise HTTPException(401, "Invalid token")

_hits = {}
def rate_limit(uid, n=8, window=60):  # use Redis/Memorystore if you run >1 instance
    now = time.time()
    h = [t for t in _hits.get(uid, []) if now - t < window]
    if len(h) >= n:
        raise HTTPException(429, "Slow down, city's not going anywhere")
    _hits[uid] = h + [now]

# ---------- helpers ----------
def hav(a, b, c, d):  # km
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

def to_wav(b: bytes) -> bytes:
    p = subprocess.run(["ffmpeg", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1", "-loglevel", "error"],
                       input=b, capture_output=True, timeout=20)
    if p.returncode:
        raise HTTPException(415, "Could not read audio")
    return p.stdout

async def rain_mm(lat, lon):  # independent signal: live precipitation (Open-Meteo, no key)
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get("https://api.open-meteo.com/v1/forecast",
                            params={"latitude": lat, "longitude": lon, "current": "precipitation"})
        return float(r.json()["current"]["precipitation"])
    except Exception:
        return None

def trust(r, peers):
    """Verification score 0-98 = AI consistency + independent corroboration + evidence + external signal."""
    s = 30 + 20 * r["ai_conf"]
    others = {p["uid"] for p in peers
              if p["id"] != r["id"] and p["uid"] != r["uid"] and p["category"] == r["category"]
              and abs(p["ts"] - r["ts"]) < 7200 and hav(p["lat"], p["lon"], r["lat"], r["lon"]) < 0.6}
    s += 15 * min(len(others), 3)
    s += 10 if r.get("has_media") else 0
    if r["category"] == "flood" and (r.get("rain_mm") or 0) > 0.5:
        s += 15
    return min(98, round(s)), len(others)

def nearby(lat, lon, km, hours=6):
    cell = pgh.encode(lat, lon, precision=4)  # ~40x20 km tile; exact radius filtered below
    docs = db.collection("reports").where("cell4", "==", cell).where("ts", ">", time.time() - hours * 3600).stream()
    out = []
    for d in docs:
        r = d.to_dict(); r["id"] = d.id
        if hav(lat, lon, r["lat"], r["lon"]) <= km:
            out.append(r)
    return out

def enrich(rs, lat, lon):
    now = time.time()
    res = []
    for r in rs:
        t, n = trust(r, rs)
        res.append({**r, "trust": t, "corroborations": n, "age_min": round((now - r["ts"]) / 60),
                    "distance_km": round(hav(lat, lon, r["lat"], r["lon"]), 2), "uid": None})
    return res

# ---------- routes ----------
@app.get("/api/config")
def config():  # Firebase web config is public by design
    return json.loads(os.environ["FIREBASE_WEB_CONFIG"])

@app.post("/api/reports")
async def create_report(text: str = Form(""), lat: float = Form(...), lon: float = Form(...),
                        media: UploadFile = File(None), u=Depends(user)):
    rate_limit(u["uid"])
    if not text.strip() and not media:
        raise HTTPException(422, "Add text or media")
    parts = []
    if media:
        data = await media.read()
        if len(data) > 8_000_000:
            raise HTTPException(413, "Max 8 MB")
        mt = media.content_type or "application/octet-stream"
        if mt.startswith("audio/webm") or mt.startswith("audio/mp4"):
            data, mt = to_wav(data), "audio/wav"
        parts.append(types.Part.from_bytes(data=data, mime_type=mt))
    parts.append(f"User text: {text.strip()[:1000]!r}\nGPS: {lat:.5f},{lon:.5f}\nUTC time: {time.strftime('%Y-%m-%d %H:%M')}")
    resp = await ai.aio.models.generate_content(
        model=MODEL, contents=parts,
        config=types.GenerateContentConfig(system_instruction=EXTRACT_SYSTEM, response_mime_type="application/json",
                                           response_schema=Report, temperature=0.2))
    rep: Report = resp.parsed
    if rep is None or rep.abuse:
        raise HTTPException(422, "Report rejected by safety filter")
    doc = {**rep.model_dump(), "uid": u["uid"], "lat": lat, "lon": lon, "cell4": pgh.encode(lat, lon, precision=4),
           "ts": time.time(), "has_media": bool(media), "rain_mm": await rain_mm(lat, lon)}
    ref = db.collection("reports").document()
    ref.set(doc)
    return {"id": ref.id, "report": rep.model_dump(),
            "emergency": "If someone is in danger, call your local emergency number now." if rep.needs_emergency else None}

@app.get("/api/reports")
def feed(lat: float, lon: float, radius_km: float = 5, u=Depends(user)):
    return enrich(nearby(lat, lon, min(radius_km, 25)), lat, lon)

@app.get("/api/insights")
async def insights(lat: float, lon: float, u=Depends(user)):
    rs = [r for r in enrich(nearby(lat, lon, 5), lat, lon) if r["trust"] >= 50]
    if not rs:
        return {"headline": "Quiet around here. Be the first to report something!", "picks": []}
    slim = [{k: r[k] for k in ("category", "severity", "trust", "age_min", "distance_km", "title", "summary")} for r in rs[:40]]
    resp = await ai.aio.models.generate_content(
        model=MODEL, contents=json.dumps(slim),
        config=types.GenerateContentConfig(system_instruction=INSIGHT_SYSTEM, response_mime_type="application/json",
                                           response_schema=Insights, temperature=0.4))
    return resp.parsed.model_dump()

class RouteReq(BaseModel):
    o_lat: float; o_lon: float; d_lat: float; d_lon: float

@app.post("/api/route")
async def safest_route(q: RouteReq, u=Depends(user)):
    if not MAPS_KEY:
        raise HTTPException(501, "MAPS_API_KEY not set")
    body = {"origin": {"location": {"latLng": {"latitude": q.o_lat, "longitude": q.o_lon}}},
            "destination": {"location": {"latLng": {"latitude": q.d_lat, "longitude": q.d_lon}}},
            "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE", "computeAlternativeRoutes": True}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post("https://routes.googleapis.com/directions/v2:computeRoutes", json=body,
                         headers={"X-Goog-Api-Key": MAPS_KEY,
                                  "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters,routes.polyline.encodedPolyline"})
    routes = r.json().get("routes", [])
    mid_lat, mid_lon = (q.o_lat + q.d_lat) / 2, (q.o_lon + q.d_lon) / 2
    span = hav(q.o_lat, q.o_lon, q.d_lat, q.d_lon) / 2 + 2
    haz = [r for r in enrich(nearby(mid_lat, mid_lon, min(span, 25)), mid_lat, mid_lon)
           if r["category"] in HAZARDS and r["trust"] >= 50]
    out = []
    for rt in routes:
        pts = polyline.decode(rt["polyline"]["encodedPolyline"])[::4]
        risk = sum(h["severity"] * h["trust"] / 100 for h in haz if any(hav(h["lat"], h["lon"], a, b) < 0.2 for a, b in pts))
        mins = int(rt["duration"].rstrip("s")) / 60
        delay = max(0, (int(rt["duration"].rstrip("s")) - int(rt.get("staticDuration", rt["duration"]).rstrip("s"))) / 60)
        out.append({"minutes": round(mins), "km": round(rt["distanceMeters"] / 1000, 1), "risk": round(risk, 1), "delay_min": round(delay),
                    "path": pts, "cost": mins + risk * 8})
    out.sort(key=lambda x: x["cost"])
    for i, o in enumerate(out):
        o["recommended"] = i == 0
    return out

import sys, explore
app.include_router(explore.build_router(sys.modules[__name__]))

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
