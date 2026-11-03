"""
STRATZ (https://stratz.com) access for the Draft Helper mod.

The API needs a personal token (free at https://stratz.com/api). The token is
entered in the mod settings and then moved into `stratz_token.txt` next to the
mod, so it does not stay in Minify's main config (which is included in debug
zips). It is never written to logs.

For now this module only probes the GraphQL schema so the exact queries for
hero matchups by position and item winrates can be written against it.
"""

import json
import os

API_URL = "https://api.stratz.com/graphql"
# STRATZ asks API clients to send this user agent together with the token
USER_AGENT = "STRATZ_API"

mod_dir = os.path.dirname(os.path.abspath(__file__))
token_path = os.path.join(mod_dir, "stratz_token.txt")

_TYPE_QUERY = """
query ($name: String!) {
  __type(name: $name) {
    name
    kind
    enumValues { name }
    fields {
      name
      args { name type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
      type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
    }
  }
}
"""

_ROOT_QUERY = "{ __schema { queryType { name } } }"


def store_token(settings_token):
    """Moves a token typed into the settings into the private file. Returns True if one was stored."""
    token = (settings_token or "").strip()
    if not token:
        return False
    with open(token_path, "w", encoding="utf-8") as file:
        file.write(token)
    return True


def load_token():
    try:
        with open(token_path, encoding="utf-8") as file:
            return file.read().strip()
    except Exception:
        return ""


def _post(token, query, variables=None, timeout=20):
    import requests

    response = requests.post(
        API_URL,
        json={"query": query, "variables": variables or {}},
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = None
    return response.status_code, payload, response.text[:300]


def _unwrap(t):
    while t and t.get("ofType") is not None and t.get("name") is None:
        t = t["ofType"]
    return t or {}


def _fmt_type(t):
    if not t:
        return "?"
    kind, name = t.get("kind"), t.get("name")
    if kind == "NON_NULL":
        return _fmt_type(t.get("ofType")) + "!"
    if kind == "LIST":
        return "[" + _fmt_type(t.get("ofType")) + "]"
    return name or "?"


INTERESTING = ("hero", "matchup", "match_up", "vs", "with", "item", "purchase", "position", "bracket", "stat")


def probe_schema(token, max_types=40):
    """
    Walks the schema from the root query through `heroStats` and returns a
    human readable dump (list of lines). Never includes the token.
    """
    lines = []
    status, payload, text = _post(token, _ROOT_QUERY)
    lines.append(f"root query HTTP {status}")
    if not payload or "data" not in payload or not payload["data"]:
        lines.append("response: " + (json.dumps(payload)[:500] if payload else text))
        return lines
    root_name = payload["data"]["__schema"]["queryType"]["name"]

    seen = set()
    queue = [(root_name, 0)]
    while queue and len(seen) < max_types:
        name, depth = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        status, payload, text = _post(token, _TYPE_QUERY, {"name": name})
        info = (payload or {}).get("data", {}).get("__type") if payload else None
        if not info:
            lines.append(f"type {name}: HTTP {status} {(json.dumps(payload) if payload else text)[:300]}")
            continue
        if info.get("enumValues"):
            lines.append(f"enum {name}: " + ", ".join(v["name"] for v in info["enumValues"]))
            continue
        lines.append(f"type {name}:")
        for field in info.get("fields") or []:
            args = ", ".join(f"{a['name']}: {_fmt_type(a['type'])}" for a in field.get("args") or [])
            lines.append(f"  {field['name']}({args}) -> {_fmt_type(field['type'])}")
            follow = depth == 0 and field["name"] in ("heroStats", "constants")
            follow = follow or (depth >= 1 and depth < 3 and any(k in field["name"].lower() for k in INTERESTING))
            if follow:
                target = _unwrap(field["type"]).get("name")
                if target and target not in seen:
                    queue.append((target, depth + 1))
            if depth >= 1:
                for a in field.get("args") or []:
                    arg_type = _unwrap(a["type"])
                    if arg_type.get("kind") == "ENUM" and arg_type.get("name") not in seen:
                        queue.append((arg_type["name"], 99))
    return lines
