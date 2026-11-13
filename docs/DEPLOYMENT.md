# Deployment instructions — HOD Faculty Feedback modules

**Schema:** v24 (this release adds **no** new migration)  
**Zip:** `ALL_SESSION_CHANGES.zip`

## 1. Pre-flight

- Working backup of the production database (SQLite file or PostgreSQL dump).
- Python 3.11+ with project venv; `pip install -r requirements.txt` (includes `reportlab`).
- Confirm current app already runs at schema **v24** before deploying UI-only changes.

## 2. Deploy code

```bash
cd /path/to/FEEBACK_ADITYAUNIVERSITY.IN
# optional safety branch
git checkout -b deploy/hod-feedback-modules
unzip -o ALL_SESSION_CHANGES.zip
git add tests/ docs/ feedback_system/
git status   # review
git commit -m "HOD Main/Second/Lab feedback, results tabs, category PDFs, test baseline"
```

Paths are relative to the repo root. Do not nest the zip under another folder.

## 3. Database

- **No migration required** for this feature set (still v24).
- On first app start, existing migrate runner is idempotent:  
  copy DB → start app → confirm `feedback_system_schema_migrations` max version is still 24 and row counts unchanged.
- Never delete historical forms, submissions, or soft-removed second-faculty rows.

## 4. Restart

```bash
# example — adjust to your process manager
sudo systemctl restart faculty-feedback
# or
pkill -f gunicorn; gunicorn -w 4 -b 0.0.0.0:8000 'feedback_system:create_app()'
```

## 5. Smoke checks (production)

1. HOD login → **Overview** loads with category cards.
2. Nav: Main / Second / Lab Faculty Feedback, Results, Reports (existing links).
3. Results tabs `?tab=main|second|lab` and **Download PDF** for the active tab.
4. Teacher login: cannot open `/hod/feedback/*` or `/hod/results/*.pdf`.
5. Optional: `python -m pytest tests/test_hod_feedback_main.py tests/test_hod_feedback_lab.py tests/test_hod_feedback_second_and_results.py tests/test_hod_category_pdf.py -q`

## 6. Rollback

```bash
git revert HEAD   # or checkout previous commit
# restart app — no DB rollback needed (no schema change)
```

## 7. Notes

- Main, Second, and Lab forms stay separate (category column + separate eligibility).
- Student answers remain anonymous; eligible names only in HOD preview.
- PDF reports reuse `reports.build_category_results_pdf` and HOD scope filters.
