services:
  - type: web
    name: bridge-clearance-dashboard
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app --bind 0.0.0.0:$PORT
    envVars:
      - key: GITHUB_OWNER
        value: richardmhuse
      - key: DATA_REPO
        value: do-not-hit-the-bridge
      - key: DATA_BRANCH
        value: main
      - key: DATA_PATH
        value: data/raw/measured.csv
      - key: CACHE_TTL_SECONDS
        value: "300"
      - key: LOOKBACK_DAYS
        value: "2"
      - key: REFRESH_MS
        value: "60000"
    healthCheckPath: /healthz
