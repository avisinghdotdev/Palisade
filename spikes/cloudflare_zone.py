"""Read-only review of the Cloudflare zone: settings, DNS, bot settings, rulesets, page rules, AOP.

Run from the repo root: uv run --frozen --env-file .env python spikes/cloudflare_zone.py

Reads with the token in `CLOUDFLARE_API_TOKEN`. The checklist the output is compared against is in
`docs/plan.md` section 12.
"""

import json
import os
import sys
from typing import Any

import httpx2

ZONE_ID = "846bfdbfe84a1f02a0ece6c860e8a304"
BASE_URL = "https://api.cloudflare.com/client/v4"
PHASES = [
    "http_request_firewall_custom",
    "http_ratelimit",
    "http_request_firewall_managed",
    "http_request_cache_settings",
    "http_response_headers_transform",
    "http_config_settings",
]


class Cloudflare:
    def __init__(self, http: httpx2.Client) -> None:
        self._http = http

    def get(self, path: str) -> Any:
        """GET `path`; prints the API's errors and exits on anything but success."""
        response = self._http.get(path)
        body = response.json()
        if response.status_code // 100 != 2 or not body["success"]:
            print(
                f"GET {path} -> {response.status_code} {json.dumps(body.get('errors'))}"
            )
            sys.exit(1)
        return body["result"]

    def get_or_none(self, path: str) -> Any:
        """GET `path`; returns None when the zone has nothing there (404), otherwise as `get`."""
        response = self._http.get(path)
        if response.status_code == 404:
            return None
        return self.get(path)


def main() -> None:
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}
    with httpx2.Client(base_url=BASE_URL, headers=headers, timeout=60.0) as http:
        cloudflare = Cloudflare(http)

        print("[1] token")
        print(f"  {json.dumps(cloudflare.get('/user/tokens/verify'))}")

        print("\n[2] zone settings")
        for setting in sorted(
            cloudflare.get(f"/zones/{ZONE_ID}/settings"), key=lambda s: s["id"]
        ):
            print(f"  {setting['id']} = {json.dumps(setting['value'])}")

        print("\n[3] dns records")
        for record in cloudflare.get(f"/zones/{ZONE_ID}/dns_records?per_page=100"):
            print(
                f"  {record['type']:6} {record['name']:40} -> {record['content'][:70]} "
                f"proxied={record['proxied']}"
            )

        print("\n[4] bot management (needs the Bot Management Read permission)")
        response = http.get(f"/zones/{ZONE_ID}/bot_management")
        print(f"  {response.status_code} {json.dumps(response.json().get('result'))}")

        print("\n[5] rulesets on the zone")
        for ruleset in cloudflare.get(f"/zones/{ZONE_ID}/rulesets"):
            print(
                f"  {ruleset['phase']:36} {ruleset['kind']:8} {ruleset['name']} v{ruleset['version']}"
            )

        print("\n[6] rules per phase")
        for phase in PHASES:
            entrypoint = cloudflare.get_or_none(
                f"/zones/{ZONE_ID}/rulesets/phases/{phase}/entrypoint"
            )
            # An entrypoint ruleset with no rules omits the key.
            rules = entrypoint.get("rules", []) if entrypoint else []
            print(f"  {phase}: {len(rules)} rule(s)")
            for rule in rules:
                state = "on" if rule.get("enabled", True) else "off"
                print(
                    f"    [{state}] {rule['action']}: {rule.get('description', '')} | "
                    f"{rule['expression']} | {json.dumps(rule.get('action_parameters', {}))[:300]}"
                )

        print("\n[7] page rules")
        for page_rule in cloudflare.get(f"/zones/{ZONE_ID}/pagerules"):
            print(f"  {page_rule['status']} {json.dumps(page_rule['targets'])[:120]}")

        print("\n[8] authenticated origin pulls")
        print(
            f"  {json.dumps(cloudflare.get(f'/zones/{ZONE_ID}/origin_tls_client_auth/settings'))}"
        )


if __name__ == "__main__":
    main()
