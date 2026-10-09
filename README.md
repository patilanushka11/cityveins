CityVeins

CityVeins is a smart, safety-aware city exploration app. It combines places, heritage, weather, traffic, and citizen reports into one map-based experience.

## Features

- **Explore:** Discover food, stays, attractions, and heritage locations.
- **Weighted ranking:** Rank places by safety, cleanliness, affordability, rating, and accessibility.
- **Compare:** Compare two to four places and request an AI-generated verdict.
- **Culture:** Find nearby heritage using Wikipedia geosearch and generate a grounded culture guide.
- **Safety reports:** Submit text, photos, videos, or voice reports.
- **Trust scoring:** Combine AI consistency, independent corroboration, evidence, and live rain data.
- **Hazard map:** View nearby flood, traffic, safety, and civic issue reports.
- **Safe routes:** Compare traffic-aware routes by time, distance, and hazard risk.
- **Day planner:** Generate an itinerary from available places, weather, hazards, hours, budget, and interests.

## Architecture

```text
Browser
  Leaflet + Firebase Auth + MediaRecorder
        |
        v
FastAPI on Cloud Run
  Firestore + Gemini + Google Places API + Google Routes API
  Open-Meteo + Wikipedia
```

All `/api/*` endpoints except `/api/config` require a Firebase ID token. Firestore rules deny direct client access; the backend uses the Firebase Admin SDK.

## Repository Structure

```text
cityveins/
|-- backend/
|   |-- Dockerfile          Cloud Run container definition
|   |-- explore.py          Places, heritage, context, compare, and planner routes
|   |-- main.py             FastAPI app, auth, reports, insights, and routing
|   |-- prompts.py          Gemini system prompts
|   |-- requirements.txt    Python dependencies
|   `-- schemas.py          Report and insight schemas
|-- frontend/
|   `-- index.html          Leaflet and Firebase browser application
|-- firestore.indexes.json  Firestore indexes
|-- firestore.rules         Firestore security rules
|-- .gitignore
`-- README.md
```

## Requirements

- Python 3.12 or newer
- A Firebase project
- Google Cloud CLI for ADC and deployment
- Firebase Authentication with Google provider enabled
- Firestore in Native mode
- Places API (New)
- Routes API
- A Gemini API key
- A Maps API key
- `ffmpeg` for voice report conversion

## Firebase Setup

1. Create or select a Firebase project.
2. Enable **Authentication** and the **Google** provider.
3. Create a Firestore database in Native mode.
4. Register a Firebase Web app.
5. Add `localhost` and `127.0.0.1` to Authentication > Settings > Authorized domains.
6. Deploy the Firestore rules and indexes from the repository root:

```powershell
firebase deploy --only firestore
```

The Firebase Web configuration is safe to send to the browser. API keys used by Gemini and Google Maps must remain server-side and must not be committed.

## Google Cloud and ADC

Install the Google Cloud CLI for Windows:

https://cloud.google.com/sdk/docs/install

Open a new PowerShell window after installation, then authenticate:

```powershell
gcloud auth login
gcloud auth application-default login
```

Find your Firebase project ID in Firebase Console > Project settings. Set it as the active project and ADC quota project:

```powershell
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

If `gcloud` is not available in the current terminal, set the project environment variables manually:

```powershell
$env:GOOGLE_CLOUD_PROJECT = "YOUR_PROJECT_ID"
$env:GCLOUD_PROJECT = "YOUR_PROJECT_ID"
```

## Local Configuration

Environment variables are scoped to the terminal session. Set them in the same PowerShell window used to start Uvicorn.

```powershell
$env:GOOGLE_CLOUD_PROJECT = "YOUR_PROJECT_ID"
$env:GCLOUD_PROJECT = "YOUR_PROJECT_ID"
$env:GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
$env:MAPS_API_KEY = "YOUR_MAPS_API_KEY"
$env:FIREBASE_WEB_CONFIG = '{"apiKey":"YOUR_FIREBASE_WEB_API_KEY","authDomain":"YOUR_PROJECT_ID.firebaseapp.com","projectId":"YOUR_PROJECT_ID","storageBucket":"YOUR_STORAGE_BUCKET","messagingSenderId":"YOUR_SENDER_ID","appId":"YOUR_APP_ID"}'
```

Do not put real values in source files or commit them to GitHub. For repeated local development, use a local `.env` workflow or configure user-level environment variables; `.env` files are ignored by Git.

## Install Dependencies

From the repository root:

```powershell
cd D:\cityveins
python -m pip install -r backend\requirements.txt
```

Install `ffmpeg` separately and ensure `ffmpeg.exe` is on `PATH` if voice reports are needed.

## Run Locally

Start the backend from the `backend` directory after setting all required environment variables:

```powershell
cd D:\cityveins\backend
if (-not (Test-Path static\index.html)) {
  New-Item -ItemType Directory -Force static | Out-Null
  Copy-Item ..\frontend\index.html static\index.html
}
python -m uvicorn main:app --reload
```

Open the application at:

http://127.0.0.1:8000

Allow browser location access, then sign in with Google. To stop the server, press `Ctrl+C`.

### Local Startup Checks

Compile the Python files without starting the service:

```powershell
python -m compileall -q backend
```

Check that the Firebase config endpoint responds after the server starts:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/config -UseBasicParsing
```

## API Overview

All protected routes require:

```text
Authorization: Bearer FIREBASE_ID_TOKEN
```

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/config` | Return public Firebase Web config |
| GET | `/api/context` | Weather, alerts, and nearby hazard count |
| GET | `/api/places` | Ranked nearby places with scores and tags |
| GET | `/api/heritage` | Nearby Wikipedia heritage with culture guide |
| GET | `/api/reports` | Nearby citizen reports with trust data |
| POST | `/api/reports` | Create a text, image, video, or voice report |
| GET | `/api/insights` | AI go, avoid, and information picks |
| POST | `/api/route` | Rank traffic-aware routes by time and hazard risk |
| POST | `/api/compare` | Compare two to four places with an AI verdict |
| POST | `/api/plan` | Generate a grounded day itinerary |

## Scoring and Trust

- **Rating:** Bayesian-smoothed Google rating.
- **Affordability:** `1 - price_level / 4`.
- **Accessibility:** Wheelchair entrance flag; unknown is neutral.
- **Safety and cleanliness:** Trust-weighted reports within 300 meters.
- **No report data:** A neutral default is used and exposed as `reports_nearby`.
- **Report trust:** A 0-98 score based on AI consistency, independent users within 600 meters and two hours, evidence, and live rain for flood claims.
- **Overall score:** Weighted mean controlled by the user sliders.
- **Grounding:** Gemini receives retrieved data and selected place IDs only. Invented place IDs are discarded by the backend.

## Google Sign-In Troubleshooting

If sign-in does not work:

- `auth/operation-not-allowed`: enable Google under Firebase Authentication > Sign-in providers.
- `auth/unauthorized-domain`: add both `localhost` and `127.0.0.1` to Firebase Authorized domains.
- `auth/popup-blocked`: allow popups for the local app.
- `/api/config` returns an error: set `FIREBASE_WEB_CONFIG` in the same terminal that runs Uvicorn.
- The page cannot load: confirm Uvicorn reports `Application startup complete` and open `http://127.0.0.1:8000` rather than the HTML file directly.

## Deploy to Cloud Run

From the repository root, replace the placeholders and deploy:

```powershell
gcloud run deploy cityveins `
  --source . `
  --dockerfile backend/Dockerfile `
  --region YOUR_REGION `
  --allow-unauthenticated `
  --set-env-vars GEMINI_API_KEY=YOUR_GEMINI_KEY,MAPS_API_KEY=YOUR_MAPS_KEY,FIREBASE_WEB_CONFIG='{"apiKey":"...","authDomain":"...","projectId":"YOUR_PROJECT_ID"}'
```

Add the resulting Cloud Run URL to Firebase Authentication > Authorized domains. Use Secret Manager for production secrets instead of passing them directly on the command line.

## Publish to GitHub

Initialize and commit the project:

```powershell
git init
git add .
git commit -m "Initial CityVeins project"
git branch -M main
```

Create an empty GitHub repository, add it as the remote, and push:

```powershell
git remote add origin https://github.com/YOUR_USERNAME/cityveins.git
git push -u origin main
```

If GitHub reports that the remote contains work you do not have locally:

```powershell
git fetch origin
git merge origin/main --allow-unrelated-histories
git push -u origin main
```

Never commit `.env` files, service-account JSON files, ADC files, Gemini keys, or Maps keys. Rotate any key that has been exposed publicly.

## Demo Flow

1. Sign in and allow location.
2. Open Explore, select Food, and increase the safety weight.
3. Select two to four places and open Compare.
4. Open Culture to load nearby heritage.
5. Open Plan and request a six-hour mid-budget itinerary.
6. Submit reports from two accounts and inspect trust changes in Safety.
7. Select a map destination and request a hazard-aware route.

## Honest Limits

- Places `includedTypes` can change between Places API versions; update `GROUPS` in `backend/explore.py` if a category returns no results.
- Places caching is short-lived and intended for a demo. Check Google Places API terms before changing cache behavior.
- Wikipedia coverage is uneven and limited to notable English-language pages.
- Social-media data is not scraped; use pasted posts or official APIs.
- The rate limiter is in memory and should be replaced with Memorystore for multiple Cloud Run instances.
- There is no reputation system or offline mode yet.
