# Admin teacher photos (college Excel import) — handoff

## What changed
- **Admin → Photos** (`/admin/photos`): list of teachers/HODs/Deans with photo status, search and
  "with / without photo" filter, per-row **Add / Replace** (existing `photos.manage` page) and **Remove**.
- **Admin → Photos → Import** (`/admin/photos/import`): upload the college sheet (`.xlsx`, columns
  S.No, Time Table, Emp.Code, Emp.Name, Photo). Each embedded picture is matched to its row, then to the
  user by `Emp.Code`, and saved through the existing `photos.save_photo` (JPG/PNG check, resize,
  EXIF strip). Optional "Replace photos that already exist". The result page lists added / replaced /
  skipped / unknown Emp.Code / rejected / rows without a picture. Several sheets per file are read.
- **Teachers can no longer choose their own photo**: `photos.can_manage` is false for a teacher's own
  code; the profile page and the first-login photo step (`/setup/photo`) are read-only and the step now
  just shows the photo and continues. `/photos/me` sends teachers to `/profile`.
- HOD/Dean own-photo upload and HOD on-behalf upload for teachers of the HOD's departments are unchanged.
- No schema change (reuses `employee_photos`). New audit actions: `photo.import`, `photo.delete`.

## Files
`feedback_system/photos.py`, `auth.py` (`setup_photo`), templates `admin_photos.html`,
`admin_photos_import.html`, `setup_photo.html`, `profile.html`, `photo_manage.html`,
`admin_teachers.html`, `base.html` (admin nav "Photos").
Tests: `test_photos_phase1.py`, `test_first_login_phase2.py`, `test_profile_and_second_views.py`.

## Limits / notes
- Upload size is capped by `MAX_UPLOAD_MB` (default 10). A sheet with many photos may need a higher value.
- Only pictures anchored to a row with an Emp.Code are used; floating pictures not on a listed row are ignored.
- Emp.Codes with no active teacher/HOD/Dean account are reported, never created.
- Legacy `static/img/teachers/<code>.jpg` still shows when no stored photo exists.
