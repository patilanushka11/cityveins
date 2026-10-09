"""CityVeins v2: places discovery + scoring, heritage, city context, comparison, AI day planner."""
import os, time, json, httpx, pygeohash as pgh
from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from google.genai import types
from prompts import CULTURE_SYSTEM, PLAN_SYSTEM, COMPARE_SYSTEM

KEY = os.getenv("MAPS_API_KEY", "")
# Places API (New) type names: verify against the current Table A list if a group returns nothing.
GROUPS = {"food": ["restaurant", "cafe", "bakery"], "stay": ["lodging"],
          "attractions": ["tourist_attraction", "park", "museum"],
          "heritage": ["historical_place", "cultural_landmark", "monument"]}
PRICE = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
         "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
FIELDS = ("places.id,places.displayName,places.location,places.rating,places.userRatingCount,places.priceLevel,"
          "places.formattedAddress,places.accessibilityOptions,places.editorialSummary,places.regularOpeningHours.openNow")
DIMS = ["safety", "cleanliness", "affordability", "rating", "accessibility"]

# ----- AI schemas -----
class CultureItem(BaseModel):
    title: str; tagline: str; why_visit: str; etiquette_or_tip: str
class Culture(BaseModel):
    items: List[CultureItem]
class Stop(BaseModel):
    time: str; place_id: str; kind: str; why: str; cost_hint: str; safety_note: Optional[str] = None
class Plan(BaseModel):
    summary: str; weather_note: str; stops: List[Stop]; tips: List[str]
class Verdict(BaseModel):
    winner: str; summary: str; tradeoffs: List[str]
class PlanReq(BaseModel):
    lat: float; lon: float; hours: int = 6; budget: Literal["low", "mid", "high"] = "mid"; interests: List[str] = []
class CmpPlace(BaseModel):
    name: str; scores: dict; overall: int; reports_nearby: int = 0
class CmpReq(BaseModel):
    places: List[CmpPlace]

def build_router(m):
    r = APIRouter()

    async def gen(system, payload, schema, temp=0.3):
        resp = await m.ai.aio.models.generate_content(
            model=m.MODEL, contents=payload if isinstance(payload, str) else json.dumps(payload),
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               response_schema=schema, temperature=temp))
        if resp.parsed is None:
            raise HTTPException(502, "AI returned nothing usable")
        return resp.parsed.model_dump()

    async def fetch_places(lat, lon, group, radius):
        # Short-lived cache cuts API cost. Check Places API terms before caching beyond a demo.
        ref = m.db.collection("places_cache").document(f"{group}_{pgh.encode(lat, lon, precision=5)}")
        snap = ref.get()
        if snap.exists and time.time() - snap.to_dict()["ts"] < 6 * 3600:
            return snap.to_dict()["items"]
        if not KEY:
            raise HTTPException(501, "MAPS_API_KEY not set")
        body = {"includedTypes": GROUPS[group], "maxResultCount": 20, "rankPreference": "POPULARITY",
                "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": float(radius)}}}
        async with httpx.AsyncClient(timeout=10) as c:
            res = await c.post("https://places.googleapis.com/v1/places:searchNearby", json=body,
                               headers={"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": FIELDS})
        items = [{"id": p["id"], "name": p["displayName"]["text"], "lat": p["location"]["latitude"],
                  "lon": p["location"]["longitude"], "rating": p.get("rating"), "n": p.get("userRatingCount", 0),
                  "price": PRICE.get(p.get("priceLevel")), "address": p.get("formattedAddress"), "group": group,
                  "wheelchair": (p.get("accessibilityOptions") or {}).get("wheelchairAccessibleEntrance"),
                  "blurb": (p.get("editorialSummary") or {}).get("text"),
                  "open": (p.get("regularOpeningHours") or {}).get("openNow")} for p in res.json().get("places", [])]
        ref.set({"ts": time.time(), "items": items})
        return items

    def score(p, reps, w):
        """Transparent 0-100 scores. Safety/cleanliness come from trust-weighted citizen reports within 300 m."""
        near = [x for x in reps if m.hav(p["lat"], p["lon"], x["lat"], x["lon"]) < 0.3]
        haz = sum(x["severity"] * x["trust"] / 100 for x in near if x["category"] in m.HAZARDS)
        safety = max(0, 1 - haz / 3) if near else 0.75          # no data -> neutral-ish default
        cl = [x for x in near if x["category"] == "cleanliness"]
        delta = sum({"positive": .2, "negative": -.25}.get(x["sentiment"], 0) * x["trust"] / 100 for x in cl)
        clean = min(1, max(0, .6 + delta)) if cl else .6
        rating = ((p["rating"] * p["n"] + 4.0 * 20) / (p["n"] + 20)) / 5 if p["rating"] else .5  # Bayesian-smoothed
        afford = .5 if p["price"] is None else 1 - p["price"] / 4
        acc = {True: 1, False: .1, None: .5}[p["wheelchair"]]
        dims = dict(safety=safety, cleanliness=clean, affordability=afford, rating=rating, accessibility=acc)
        tot = sum(w[k] for k in DIMS) or 1
        overall = sum(dims[k] * w[k] for k in DIMS) / tot
        return {**p, "scores": {k: round(v * 100) for k, v in dims.items()}, "overall": round(overall * 100),
                "reports_nearby": len(near)}

    def trusted_reports(lat, lon, km):
        return [x for x in m.enrich(m.nearby(lat, lon, min(km, 25)), lat, lon) if x["trust"] >= 40]

    async def ranked(lat, lon, group, radius, w, max_price):
        items = await fetch_places(lat, lon, group, radius)
        reps = trusted_reports(lat, lon, radius / 1000 + 1)
        out = [score(p, reps, w) for p in items if p["price"] is None or p["price"] <= max_price]
        return sorted(out, key=lambda x: -x["overall"])

    async def get_context(lat, lon):
        async with httpx.AsyncClient(timeout=6) as c:
            d = (await c.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_hours": 8,
                "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
                "hourly": "precipitation_probability"})).json()
        cur, h = d["current"], d["hourly"]
        alerts = []
        for t, p in zip(h["time"], h["precipitation_probability"]):
            if p >= 60:
                alerts.append(f"Rain likely around {t[-5:]} ({p}%)"); break
        if cur["wind_speed_10m"] >= 40: alerts.append("Strong winds: avoid open viewpoints")
        if cur["temperature_2m"] >= 38: alerts.append("Heat alert: plan indoor stops midday")
        n = len([x for x in trusted_reports(lat, lon, 3) if x["category"] in m.HAZARDS and x["trust"] >= 50])
        return {"time": cur["time"], "temp_c": cur["temperature_2m"], "precip_mm": cur["precipitation"],
                "wind_kmh": cur["wind_speed_10m"], "alerts": alerts, "hazards_nearby": n}

    @r.get("/api/context")
    async def context(lat: float, lon: float, u=Depends(m.user)):
        return await get_context(lat, lon)

    @r.get("/api/places")
    async def places(lat: float, lon: float, group: str = "food", radius_m: int = 3000, max_price: int = 4,
                     w_safety: float = 1, w_cleanliness: float = 1, w_affordability: float = 1, w_rating: float = 1,
                     w_accessibility: float = 1, u=Depends(m.user)):
        if group not in GROUPS:
            raise HTTPException(422, "Unknown group")
        w = {k: max(0, min(5, v)) for k, v in zip(DIMS, [w_safety, w_cleanliness, w_affordability, w_rating, w_accessibility])}
        out = await ranked(lat, lon, group, min(radius_m, 10000), w, max_price)
        for i, o in enumerate(out):
            o["tag"] = "best" if i < 3 else ("worst" if len(out) > 6 and i >= len(out) - 3 else None)
        return out

    @r.get("/api/heritage")
    async def heritage(lat: float, lon: float, u=Depends(m.user)):
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "CityVeins/1.0 (hackathon)"}) as c:
            res = await c.get("https://en.wikipedia.org/w/api.php", params={
                "action": "query", "format": "json", "generator": "geosearch", "ggscoord": f"{lat}|{lon}",
                "ggsradius": 8000, "ggslimit": 12, "prop": "extracts|coordinates", "exintro": 1,
                "explaintext": 1, "exsentences": 3, "exlimit": "max"})
        pages = (res.json().get("query") or {}).get("pages", {}).values()
        items = [{"title": p["title"], "extract": p["extract"], "lat": p["coordinates"][0]["lat"],
                  "lon": p["coordinates"][0]["lon"]} for p in pages if p.get("extract") and p.get("coordinates")]
        if not items:
            return []
        out = await gen(CULTURE_SYSTEM, [{"title": i["title"], "extract": i["extract"]} for i in items], Culture)
        pos = {i["title"]: i for i in items}
        return [{**c, "lat": pos[c["title"]]["lat"], "lon": pos[c["title"]]["lon"]} for c in out["items"] if c["title"] in pos]

    @r.post("/api/compare")
    async def compare(q: CmpReq, u=Depends(m.user)):
        m.rate_limit(u["uid"], n=6)
        if not 2 <= len(q.places) <= 4:
            raise HTTPException(422, "Pick 2-4 places")
        return await gen(COMPARE_SYSTEM, [p.model_dump() for p in q.places], Verdict, 0.2)

    @r.post("/api/plan")
    async def plan(q: PlanReq, u=Depends(m.user)):
        m.rate_limit(u["uid"], n=4)
        maxp = {"low": 1, "mid": 2, "high": 4}[q.budget]
        w = {k: 1 for k in DIMS}
        cands = []
        for g in ("food", "attractions", "heritage"):
            cands += (await ranked(q.lat, q.lon, g, 3000, w, maxp))[:8]
        ctx = await get_context(q.lat, q.lon)
        slim = [{k: p[k] for k in ("id", "name", "group", "rating", "price", "scores", "overall", "open", "lat", "lon")} for p in cands]
        out = await gen(PLAN_SYSTEM, {"prefs": {"hours": min(q.hours, 12), "budget": q.budget, "interests": q.interests[:6]},
                                      "local_time": ctx["time"], "weather": ctx, "hazards_nearby": ctx["hazards_nearby"],
                                      "candidates": slim}, Plan, 0.5)
        byid = {p["id"]: p for p in cands}
        stops = []
        for s in out["stops"]:                       # drop anything the model invented
            p = byid.get(s["place_id"])
            if p:
                stops.append({**s, "name": p["name"], "lat": p["lat"], "lon": p["lon"], "scores": p["scores"]})
        return {**out, "stops": stops, "context": ctx}

    return r
