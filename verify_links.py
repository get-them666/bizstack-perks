import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "perks.json"
USER_AGENT = "Mozilla/5.0 (BizStackPerks Link Verifier)"


def load_partners() -> list[dict[str, str]]:
    try:
        with DATA_PATH.open(encoding="utf-8") as file:
            partners = json.load(file)
    except FileNotFoundError:
        print(f"Error: affiliate data file not found: {DATA_PATH}", file=sys.stderr)
        return []
    except json.JSONDecodeError as exc:
        print(f"Error: affiliate data file contains invalid JSON: {exc}", file=sys.stderr)
        return []

    if not isinstance(partners, list):
        print("Error: affiliate data must be a JSON array", file=sys.stderr)
        return []
    return partners


def verify_link(name: str, url: str) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.getcode()
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        print(f"FAIL {name}: HTTP {exc.code}")
        return False
    except urllib.error.URLError as exc:
        print(f"FAIL {name}: connection error: {exc.reason}")
        return False

    if 200 <= status < 400:
        redirect_note = f" -> {final_url}" if final_url != url else ""
        print(f"OK   {name}: HTTP {status}{redirect_note}")
        return True

    print(f"FAIL {name}: HTTP {status}")
    return False


def main() -> int:
    partners = load_partners()
    if not partners:
        return 1

    print("Starting affiliate link verification")
    failures = 0
    for partner in partners:
        name = partner.get("name")
        url = partner.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            print("FAIL invalid affiliate entry: name and url are required")
            failures += 1
            continue
        failures += not verify_link(name, url)

    print(f"Verification complete: {len(partners) - failures} healthy, {failures} failed")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
