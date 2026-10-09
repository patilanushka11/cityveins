# CityVeins v2: smart, safe, joyful city exploration

## Problem-statement coverage
| PS pillar | What CityVeins does | Where |
|---|---|---|
| Exploration & hospitality | Attractions, food, stays, budget filter, open-now, ranked by your own weights | `GET /api/places` (Places API New) |
| History & culture | Nearby heritage from Wikipedia geosearch; Gemini writes a short culture guide **only from the extract** | `GET /api/heritage` |
| Safety & security | Citizen reports (text/voice/photo/video) → AI structuring → trust score → hazard map, avoid/go picks, hazard-aware route ranking | `/api/reports`, `/api/insights`, `/api/route` |
| Best vs worst | Per-place 0-100 scores for safety, cleanliness, affordability, rating, accessibility; best/worst tags; side-by-side compare + AI verdict | `/api/places`, `/api/compare` |
| Smart city insights | Live weather and alerts (Open-Meteo), traffic delay on routes (Routes API), citizen reports, AI day planner grounded in all of it | `/api/context`, `/api/plan` |

## Architecture
Browser (Leaflet, Firebase Auth, MediaRecorder) → Cloud Run FastAPI → Gemini · Firestore · Places API · Routes API · Open-Meteo · Wikipedia.
Every `/api/*` call requires a Firebase ID token; Firestore rules deny all client access (Admin SDK only).

## How scores and trust work (be ready to explain this to judges)
- **Rating** = Bayesian-smoothed Google rating. **Affordability** = 1 - price_level/4. **Accessibility** = wheelchair-entrance flag (unknown = neutral).
- **Safety / cleanliness** = trust-weighted citizen reports within 300 m. No reports = neutral default, and the UI says so (`reports_nearby`).
- **Report trust (0-98)** = AI media/text consistency + independent users nearby (600 m, 2 h) + attached evidence + live-rain check for flood claims.
- **Overall** = weighted mean; weights are the user's sliders.
- **Grounding**: Gemini only ever sees retrieved data and must pick places by ID; the backend drops any ID it didn't supply.

## Setup
1. Firebase: new project, enable Auth → Google, create Firestore (Native), register a web app.
2. Google Cloud: enable **Places API (New)** and **Routes API**; create a Maps key. Get a Gemini key from AI Studio.
3. `firebase deploy --only firestore` (rules + index).
4. From the repo root:
```
gcloud run deploy cityveins --source . --dockerfile backend/Dockerfile --region <region> --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=...,MAPS_API_KEY=...,FIREBASE_WEB_CONFIG='{"apiKey":"...","authDomain":"...","projectId":"..."}'
```
5. Add the Cloud Run URL to Firebase Auth → Authorized domains. Use Secret Manager for keys beyond the demo.
Local: `cd backend && mkdir -p static && cp ../frontend/index.html static/ && uvicorn main:app --reload` (with `GOOGLE_APPLICATION_CREDENTIALS`).

## Demo script (3 minutes)
1. Explore → Food, drag the weights (safety up) → ranking reorders; Best/Worst tags.
2. Tick 3 places → Compare → AI verdict.
3. Culture → heritage cards. Plan → "6 hours, mid, street food" → itinerary with weather note.
4. Two accounts report the same flood → trust jumps to Verified → Safety tab shows the hazard circle and the route avoids it.

## Honest limits
- Places `includedTypes` names can differ by API version; adjust `GROUPS` in `explore.py` if a category is empty.
- Places cache is short-lived for demo cost; check Places API terms before caching in production.
- Wikipedia coverage is uneven (English, notable sites only). Social-media data enters via pasted posts or official APIs, no scraping.
- In-memory rate limiter (use Memorystore), no reputation system, no offline mode yet.
