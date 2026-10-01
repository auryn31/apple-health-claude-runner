---
name: apple-health-data
description: Get Apple Health data into this project and parse it. Use when the user asks to import, refresh, re-export, or parse Apple Health data, mentions export.xml or apple_health_export, when a script reports missing/stale health data, or before any analysis that needs fresh numbers (weekly check-in, dashboard, health deep-dive).
---

# Apple Health data: get it in, parse it

## 1. Check what's already there

```bash
ls -la apple_health_export/export.xml
```

If it exists, report its mtime. Anything older than ~7 days is stale for a weekly check-in — say so and offer step 2.

## 2. If missing or stale, ask the user to export

The export only happens on the iPhone — you cannot do it for them. Give them these steps verbatim:

1. iPhone → **Health** app → profile picture (top right) → **Export All Health Data** → **Export**
2. Share the `export.zip` to the Mac (AirDrop, Files, iCloud Drive)
3. Unzip it and move the folder so the layout is:

```
<project root>/apple_health_export/
├── export.xml            ← the one that matters
└── export_cda.xml
```

Copy-paste helper for them (adjust if the zip landed elsewhere):

```bash
unzip -o ~/Downloads/export.zip -d .
```

Note: the export is gitignored (`apple_health_export/`, `*.zip`) and must stay that way — it's raw personal health data.

## 3. Set up the environment (first run only)

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp env.example env   # then edit `env` with real race date, paces, schedule
```

`env` is gitignored. All plan config lives there; nothing is hardcoded.

## 4. Parse it

```bash
source venv/bin/activate
python weekly_checkin.py              # CLI report, last 4 weeks (--weeks N for more)
python webapp.py                      # dashboard on http://localhost:5050
python analyze_health.py              # deep dive, occasional
```

Every script takes `--export PATH` if the file lives somewhere else. Results are cached in `_cache/`, auto-invalidated when `env` changes.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "export not found" | Step 2 — folder in the wrong place or not unzipped |
| Numbers look weeks old | Stale export, re-export from the phone |
| Config change had no effect | You edited `env.example`, not `env` |
| Import errors | venv not activated |

## Then what

Parsing is not the goal. Once the data is in, hand off to the coaching skill for the actual assessment.
