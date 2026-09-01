"""Run staging QA through the guarded eval API without local DB composition."""

from __future__ import annotations

import argparse
import json
import urllib.request


def request_json(url: str, *, method: str = "GET", payload: object | None = None) -> object:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--id", action="append", dest="ids")
    parser.add_argument("--failed-only", action="store_true")
    args = parser.parse_args()
    payload: dict[str, object] = {}
    if args.ids:
        payload["ids"] = args.ids
    if args.failed_only:
        payload["failed_only"] = True
    result = request_json(args.base_url.rstrip("/") + "/v1/eval/run", method="POST", payload=payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if isinstance(result, dict) and result.get("fail") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
