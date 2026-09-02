"""Read-only NCP credit balance check via the Billing API.

Prints each credit's name, remaining amount, and validity, plus the total.
Requires NCP_ACCESS_KEY / NCP_SECRET_KEY in the environment or project .env.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BASE = "https://billingapi.apigw.ntruss.com"
URI = "/billing/v1/discount/getCreditHistoryList?responseFormatType=json&pageSize=100"


def load_env_keys() -> tuple[str, str]:
    access = os.getenv("NCP_ACCESS_KEY", "")
    secret = os.getenv("NCP_SECRET_KEY", "")
    if not access or not secret:
        for line in (PROJECT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("NCP_ACCESS_KEY="):
                access = line.split("=", 1)[1].strip()
            elif line.startswith("NCP_SECRET_KEY="):
                secret = line.split("=", 1)[1].strip()
    if not access or not secret:
        raise SystemExit("NCP_ACCESS_KEY / NCP_SECRET_KEY not configured")
    return access, secret


def signed_headers(method: str, uri: str, access: str, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    message = f"{method} {uri}\n{timestamp}\n{access}"
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    return {
        "x-ncp-apigw-timestamp": timestamp,
        "x-ncp-iam-access-key": access,
        "x-ncp-apigw-signature-v2": signature,
        "Accept": "application/json",
    }


def remaining_credits() -> list[dict[str, object]]:
    access, secret = load_env_keys()
    request = urllib.request.Request(BASE + URI, headers=signed_headers("GET", URI, access, secret))
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    body = payload.get("getCreditHistoryListResponse") or payload.get("getCreditHistoryList") or payload
    rows = []
    for history in (body.get("creditHistoryList") or []):
        credit = history.get("credit") or {}
        rows.append({
            "name": credit.get("creditName"),
            "received": credit.get("receivedCredit"),
            "remaining": credit.get("remainingCredit"),
            "valid_until": credit.get("validityEndMonth"),
        })
    return rows


def main() -> int:
    rows = remaining_credits()
    total = 0
    for row in rows:
        print(f"- {row['name']}: 잔여 {row['remaining']:,}원 / 받은 {row['received']:,}원 (유효 ~{row['valid_until']})")
        try:
            total += int(row["remaining"])
        except (TypeError, ValueError):
            pass
    print(f"TOTAL_REMAINING={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
