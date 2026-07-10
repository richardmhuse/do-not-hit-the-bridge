# Mirror workflow — for do-not-hit-the-bridge

This folder's contents belong in the **`do-not-hit-the-bridge`** repo
(public), not in the web app repo. It copies just the mirror path:

```
do-not-hit-the-bridge/
└── .github/
    └── workflows/
        └── mirror.yml
```

## What it does

On a schedule (every 15 minutes) and on manual trigger, it:

1. Checks out `do-not-hit-the-bridge` itself.
2. Checks out just `data/raw/measured.csv` from the private
   `NOAA-Data-Pipeline-V2` repo, using a read-only token.
3. Copies it to `data/measured.csv` in `do-not-hit-the-bridge`.
4. Commits and pushes — only if the file actually changed, so you won't
   get an empty commit every 15 minutes when the tide gauge hasn't
   reported anything new.

## Setup

1. **Copy the file.** Put `.github/workflows/mirror.yml` (from this
   folder) into the root of `do-not-hit-the-bridge`.

2. **Create a read-only token for the private repo.**
   - GitHub → Settings → Developer settings → Personal access tokens →
     Fine-grained tokens → Generate new token.
   - Resource owner: `richardmhuse`.
   - Repository access: **Only select repositories** → `NOAA-Data-Pipeline-V2`.
   - Permissions: **Contents → Read-only**. Nothing else needed.
   - Set an expiration you're comfortable renewing (fine-grained tokens
     can't be set to "no expiration").

3. **Add it as a secret on `do-not-hit-the-bridge`.**
   - `do-not-hit-the-bridge` → Settings → Secrets and variables → Actions
     → New repository secret.
   - Name: `SOURCE_REPO_TOKEN`
   - Value: the token from step 2.

4. **Run it once manually** to confirm it works: Actions tab →
   "Mirror measured.csv from NOAA-Data-Pipeline-V2" → Run workflow.
   Check that `data/measured.csv` appears in the repo afterward.

## When the token expires

The workflow will start failing with an auth error in the Actions log.
Generate a new fine-grained token (same steps as above) and update the
`SOURCE_REPO_TOKEN` secret value — no code changes needed.

## Adjusting the schedule

Edit the `cron` line in `mirror.yml`. GitHub Actions schedules can drift
by a few minutes under load, especially on the free/public runner queue,
so treat "every 15 minutes" as approximate rather than exact.
