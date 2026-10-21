"""Import a semester timetable workbook from the command line.

Use this for very large files (the web upload on Vercel is limited to ~4.5 MB
and a short function timeout).  Connect with the DIRECT database string
(port 5432) in your .env file.

    python scripts/import_excel.py path/to/Semester_IV.xlsx --semester 4 --year 2025-26
    python scripts/import_excel.py path/to/file.xlsx --semester 4 --year 2025-26 --check
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app  # noqa: E402
from feedback_system.import_data import (ImportErrorReport, import_timetable,  # noqa: E402
                                         read_workbook, summarize,
                                         validate_semester_year)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('file')
    ap.add_argument('--semester', required=True)
    ap.add_argument('--year', required=True, help='e.g. 2025-26')
    ap.add_argument('--check', action='store_true', help='validate only, write nothing')
    args = ap.parse_args()

    try:
        sem, year = validate_semester_year(args.semester, args.year)
        raw, names, report = read_workbook(args.file)
    except ImportErrorReport as exc:
        sys.exit('Cannot import: ' + '; '.join(exc.problems))
    info = summarize(raw, report)
    print({k: v for k, v in info.items() if k not in ('sheets', 'warnings')})
    for w in info['warnings']:
        print('warning:', w)
    if args.check:
        return
    app = create_app({'AUTO_IMPORT': False})
    with app.app_context():
        print('Imported:', import_timetable(app, sem, year, raw_data=(raw, names)))


if __name__ == '__main__':
    main()
