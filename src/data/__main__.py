"""Run from the project root: python -m src.data [--output outputs/p001_validation.json]."""
import argparse
import json
from pathlib import Path

from . import DEFAULT_DISTANCE_TOLERANCE, WorkbookLoadError, load_excel, validate, write_report


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Read-only P001 data validation")
    parser.add_argument("--input", type=Path, default=root / "data/parts/P001/钣金加工数据表_P001.xlsx")
    parser.add_argument("--output", type=Path, help="Optional JSON below project outputs/ or data/processed/")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_DISTANCE_TOLERANCE,
                        help="Numerical distance tolerance in source coordinate units (not manufacturing tolerance)")
    args = parser.parse_args(argv)
    try:
        data = load_excel(args.input)
        report = validate(data, distance_tolerance=args.tolerance)
    except WorkbookLoadError as exc:
        report = exc.report
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        try:
            write_report(report, args.output, project_root=root)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    print(json.dumps({"passed": report.passed, "counts": report.counts,
                      "statistics": report.statistics}, ensure_ascii=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
