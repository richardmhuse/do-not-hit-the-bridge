services:
  - type: web
    name: bridge-clearance-dashboard
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app --bind 0.0.0.0:$PORT
    envVars:
      - key: GITHUB_OWNER
        value: YOUR_GITHUB_USERNAME
      - key: DATA_REPO
        value: NOAA-Data-Pipeline-V2
      - key: DATA_BRANCH
        value: main
      - key: DATA_PATH
        value: data/raw/measured.csv
      - key: CACHE_TTL_SECONDS
        value: "300"
      - key: LOOKBACK_DAYS
        value: "7"
      - key: REFRESH_MS
        value: "60000"
    healthCheckPath: /healthz
