"""Desktop entry; preserve early import errors in CI reports for windowed builds."""

import json
import sys
import traceback
from pathlib import Path


def run():
    try:
        from heytap_eq.app import main

        return main()
    except Exception:
        if "--report" in sys.argv:
            report = Path(sys.argv[sys.argv.index("--report")+1])
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({"status": "failed", "error": traceback.format_exc()},
                                         indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(run())
