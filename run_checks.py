"""Run every check after any change to code, prompts, rules, or the model.

    python run_checks.py            no-cost checks (about 1 minute): code compiles, unit checks, harness rules,
                                    and every sourced quote against the live official pages
    python run_checks.py --offline  the same without the internet (skips the source check)
    python run_checks.py --full     also the AI tests: red team, life-safety and loosely worded requests,
                                    fairness (fresh AI calls; about $0.05 in total)

Exits with status 1 if anything fails. Logs from these runs go to a temporary folder, never to logs/.
"""

import argparse
import compileall
import importlib
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def unit_checks() -> bool:
    ok = True
    mod = importlib.import_module("tests.test_units")
    for name in [n for n in dir(mod) if n.startswith("test_")]:
        # Each check gets its own empty logs folder.
        from civicequity import config
        config.LOG_DIR = Path(tempfile.mkdtemp(prefix="civicequity-unit-"))
        for m in ("audit", "alerts", "llm", "limits", "request_bank"):
            mm = importlib.import_module(f"civicequity.{m}")
            for attr, fname in (("AUDIT_LOG", "audit_log.jsonl"), ("ALERTS_FILE", "alerts.jsonl"),
                                ("USAGE_LOG", "usage.jsonl"), ("RATE_FILE", "rate.jsonl"),
                                ("BANK_FILE", "request_bank.jsonl")):
                if hasattr(mm, attr):
                    setattr(mm, attr, config.LOG_DIR / fname)
        try:
            getattr(mod, name)()
            print(f"  PASS  {name}")
        except Exception:
            ok = False
            print(f"  FAIL  {name}\n{traceback.format_exc()}")
    return ok


def script(path: str, *args) -> bool:
    r = subprocess.run([sys.executable, path, *args], cwd=ROOT, env=os.environ.copy())
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("CIVICEQUITY_LOG_DIR", tempfile.mkdtemp(prefix="civicequity-checks-"))
    sys.path.insert(0, str(ROOT))
    results = {}
    print("1. Code compiles")
    results["compile"] = all(compileall.compile_dir(ROOT / d, quiet=1) for d in ("civicequity", "views", "analysis",
                                                                                  "redteam", "tests")) \
        and compileall.compile_file(ROOT / "app.py", quiet=1)
    print("2. Unit checks")
    results["units"] = unit_checks()
    print("3. Harness rules")
    results["harness"] = script("analysis/harness_check.py")
    if not a.offline:
        print("4. Sources, word for word")
        results["sources"] = script("analysis/verify_sources.py")
    if a.full:
        print("5. Red team (AI)")
        results["red_team"] = script("redteam/run_redteam.py")
        print("6. Life safety and loosely worded requests (AI)")
        results["life_safety"] = script("analysis/ambiguity_check.py", "after")
        print("7. Fairness (AI)")
        results["fairness"] = script("analysis/fairness_check.py")
    print("\nSummary:")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    if a.full:
        print("  (AI test scores are in redteam/results.md, analysis/ambiguity_check_after.md, "
              "analysis/fairness_check.md; review them: a passing run means they completed.)")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
