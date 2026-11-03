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
import time

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


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

CACHE_MAX_AGE = 12 * 3600
ITEM_MIN_COST = 1000
ITEMS_PER_HERO = 6
POSITION_SHARE_FOR_ITEMS = 0.15
REQUEST_PAUSE = 0.3


class StratzError(Exception):
    pass


def query(token, gql, variables=None):
    """Runs a GraphQL query and returns `data`, raising StratzError on any problem."""
    for attempt in range(3):
        try:
            status, payload, text = _post(token, gql, variables, timeout=40)
        except Exception as e:
            raise StratzError(f"{type(e).__name__}: {e}") from e
        if status == 429 and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        if status != 200:
            message = (payload or {}).get("message") if isinstance(payload, dict) else None
            raise StratzError(f"HTTP {status}: {message or text[:200]}")
        if not isinstance(payload, dict):
            raise StratzError("invalid JSON response")
        if payload.get("errors") and not payload.get("data"):
            raise StratzError(str(payload["errors"][0].get("message", payload["errors"][0]))[:300])
        time.sleep(REQUEST_PAUSE)
        return payload.get("data") or {}
    raise StratzError("rate limited")


def fetch_positions(token, bracket):
    """{heroId: {1..5: [matches, wins]}} for the bracket."""
    gql = (
        "query($b: [RankBracketBasicEnum]) { heroStats { "
        "stats(bracketBasicIds: $b, groupByPosition: true) { heroId position matchCount winCount } } }"
    )
    data = query(token, gql, {"b": [bracket]})
    out = {}
    for row in (data.get("heroStats") or {}).get("stats") or []:
        pos = str(row.get("position") or "")
        if not pos.startswith("POSITION_"):
            continue
        p = int(pos[-1])
        hero = out.setdefault(int(row["heroId"]), {})
        cell = hero.setdefault(p, [0, 0])
        cell[0] += int(row.get("matchCount") or 0)
        cell[1] += int(row.get("winCount") or 0)
    return out


def fetch_matchups(token, bracket, hero_ids, chunk=20):
    """{heroId: {"vs": {heroId2: [synergy, matches, wins]}, "with": {...}}}"""
    gql = (
        "query($ids: [Short], $b: [RankBracketBasicEnum]) { heroStats { "
        "matchUp(heroIds: $ids, bracketBasicIds: $b, take: 200) { heroId "
        "vs { heroId2 matchCount winCount synergy } with { heroId2 matchCount winCount synergy } } } }"
    )
    out = {}
    for i in range(0, len(hero_ids), chunk):
        ids = hero_ids[i : i + chunk]
        data = query(token, gql, {"ids": ids, "b": [bracket]})
        for row in (data.get("heroStats") or {}).get("matchUp") or []:
            entry = out.setdefault(int(row["heroId"]), {"vs": {}, "with": {}})
            for kind in ("vs", "with"):
                for m in row.get(kind) or []:
                    if m.get("heroId2") is None or m.get("synergy") is None:
                        continue
                    entry[kind][int(m["heroId2"])] = [
                        float(m["synergy"]),
                        int(m.get("matchCount") or 0),
                        int(m.get("winCount") or 0),
                    ]
    return out


def fetch_item_names(token):
    """{itemId: [name, cost, isRecipe]}"""
    data = query(token, "{ constants { items { id name stat { cost isRecipe } } } }")
    out = {}
    for item in (data.get("constants") or {}).get("items") or []:
        if item.get("id") is None or not item.get("name"):
            continue
        stat = item.get("stat") or {}
        out[int(item["id"])] = [item["name"], int(stat.get("cost") or 0), bool(stat.get("isRecipe"))]
    return out


def fetch_items(token, bracket, pairs, chunk=8):
    """{"heroId:pos": {itemId: [matches, wins]}} using aliased queries, `chunk` hero/position pairs per request."""
    out = {}
    for i in range(0, len(pairs), chunk):
        fields = []
        for hero_id, pos in pairs[i : i + chunk]:
            fields.append(
                f"h{hero_id}_{pos}: itemFullPurchase(heroId: {int(hero_id)}, bracketBasicIds: [{bracket}], "
                f"positionIds: [POSITION_{int(pos)}]) {{ itemId instance matchCount winCount }}"
            )
        data = query(token, "{ heroStats { " + " ".join(fields) + " } }")
        for alias, rows in (data.get("heroStats") or {}).items():
            hero_id, pos = alias[1:].split("_")
            first = [r for r in rows or [] if r.get("instance") in (None, 1)] or (rows or [])
            agg = out.setdefault(f"{hero_id}:{pos}", {})
            for r in first:
                cell = agg.setdefault(int(r["itemId"]), [0, 0])
                cell[0] += int(r.get("matchCount") or 0)
                cell[1] += int(r.get("winCount") or 0)
    return out


def _cache_path(cache_dir, bracket):
    return os.path.join(cache_dir, f"stratz_{bracket}.json")


def load_cache(cache_dir, bracket):
    try:
        with open(_cache_path(cache_dir, bracket), encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return None


def refresh(token, bracket, hero_ids, cache_dir, force=False, log=None):
    """
    Returns (status, raw) where status is "fresh", "cached" or "missing".
    Uses the cache if it is younger than CACHE_MAX_AGE (unless forced) or if the API fails.
    """
    cached = load_cache(cache_dir, bracket)
    if cached and not force and time.time() - cached.get("fetched", 0) < CACHE_MAX_AGE:
        return "cached", cached
    if not token:
        return ("cached", cached) if cached else ("missing", None)
    try:
        positions = fetch_positions(token, bracket)
        matchups = fetch_matchups(token, bracket, hero_ids)
        names = fetch_item_names(token)
        pairs = []
        for hero_id, by_pos in positions.items():
            total = sum(v[0] for v in by_pos.values()) or 1
            ranked = sorted(by_pos.items(), key=lambda kv: kv[1][0], reverse=True)
            for i, (p, v) in enumerate(ranked[:3]):
                if i == 0 or v[0] / total >= POSITION_SHARE_FOR_ITEMS:
                    pairs.append((hero_id, p))
        items = fetch_items(token, bracket, pairs)
        raw = {
            "fetched": int(time.time()),
            "bracket": bracket,
            "positions": {str(k): {str(p): v for p, v in d.items()} for k, d in positions.items()},
            "matchups": {
                str(k): {kind: {str(h): v for h, v in d[kind].items()} for kind in d} for k, d in matchups.items()
            },
            "itemNames": {str(k): v for k, v in names.items()},
            "items": {k: {str(i): v for i, v in d.items()} for k, d in items.items()},
        }
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir, bracket), "w", encoding="utf-8") as file:
            json.dump(raw, file)
        return "fresh", raw
    except StratzError as e:
        if log:
            log(f"[Draft Helper] STRATZ request failed: {e}")
        return ("cached", cached) if cached else ("missing", None)


# ---------------------------------------------------------------------------
# Turning the raw cache into the compact dataset the Panorama script reads
# ---------------------------------------------------------------------------


def _sign_from_winrates(entries):
    """
    +1 if `synergy` grows with heroId1's winrate in these entries, -1 otherwise.
    Guards against misreading STRATZ's sign convention.
    """
    xs, ys = [], []
    for synergy, matches, wins in entries:
        if matches >= 50:
            xs.append(synergy)
            ys.append(wins / matches)
    if len(xs) < 20:
        return 1
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return 1 if cov >= 0 else -1


def _std(values):
    values = [v for v in values if v]
    if len(values) < 2:
        return 1.0
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5 or 1.0


def build_section(raw, heroes):
    """Builds MDH_DATA.stratz, indexed like dotapicker's hero list (`heroes`)."""
    n = len(heroes)
    index = {h["id"]: i for i, h in enumerate(heroes)}
    matchups = raw.get("matchups") or {}

    signs = {}
    for kind in ("vs", "with"):
        entries = [v for d in matchups.values() for v in (d.get(kind) or {}).values()]
        signs[kind] = _sign_from_winrates(entries)

    flat = {"vs": [0] * (n * n), "with": [0] * (n * n)}
    for hero_id, d in matchups.items():
        c = index.get(int(hero_id))
        if c is None:
            continue
        for kind in ("vs", "with"):
            for other, v in (d.get(kind) or {}).items():
                o = index.get(int(other))
                if o is None or v[1] < 30:  # too few games to trust
                    continue
                flat[kind][c * n + o] = int(round(signs[kind] * v[0] * 10))

    share = [[0] * 5 for _ in range(n)]
    pos_wr = [[0] * 5 for _ in range(n)]
    for hero_id, by_pos in (raw.get("positions") or {}).items():
        c = index.get(int(hero_id))
        if c is None:
            continue
        total = sum(v[0] for v in by_pos.values()) or 1
        for p, (matches, wins) in by_pos.items():
            p = int(p)
            if 1 <= p <= 5 and matches:
                share[c][p - 1] = int(round(matches / total * 100))
                pos_wr[c][p - 1] = int(round(wins / matches * 1000))

    names = {int(k): v for k, v in (raw.get("itemNames") or {}).items()}
    items = {}
    for key, by_item in (raw.get("items") or {}).items():
        hero_id, pos = key.split(":")
        pos_games = 0
        hero_pos = (raw.get("positions") or {}).get(hero_id, {}).get(pos)
        if hero_pos:
            pos_games = hero_pos[0]
        min_games = max(30, int(pos_games * 0.03))
        ranked = []
        for item_id, (matches, wins) in by_item.items():
            info = names.get(int(item_id))
            if not info or matches < min_games:
                continue
            name, cost, is_recipe = info
            if is_recipe or cost < ITEM_MIN_COST or name.startswith("item_recipe"):
                continue
            ranked.append([name, int(round(wins / matches * 1000)), matches])
        ranked.sort(key=lambda r: r[1], reverse=True)
        if ranked:
            items[f"{hero_id}:{pos}"] = ranked[:ITEMS_PER_HERO]

    return {
        "bracket": raw.get("bracket", ""),
        "updated": int(raw.get("fetched", 0)),
        "vs": flat["vs"],
        "with": flat["with"],
        "share": share,
        "posWr": pos_wr,
        "items": items,
        "scale": {"vs": round(_std(flat["vs"]) / 10, 3), "with": round(_std(flat["with"]) / 10, 3)},
    }


def check(token, bracket):
    """Small end-to-end test of every query used by the mod. Returns report lines."""
    lines = []
    try:
        positions = fetch_positions(token, bracket)
        lines.append(f"positions: {len(positions)} heroes")
        sample = sorted(positions)[:3]
        matchups = fetch_matchups(token, bracket, sample)
        for h in sample:
            d = matchups.get(h, {})
            lines.append(f"matchUp hero {h}: vs {len(d.get('vs', {}))}, with {len(d.get('with', {}))}")
            first = sorted((d.get("vs") or {}).items())[:3]
            lines.append(f"  vs sample: {first}")
        names = fetch_item_names(token)
        lines.append(f"items constants: {len(names)}")
        if sample:
            hero = sample[0]
            pos = max(positions[hero].items(), key=lambda kv: kv[1][0])[0]
            items = fetch_items(token, bracket, [(hero, pos)])
            rows = items.get(f"{hero}:{pos}", {})
            lines.append(f"itemFullPurchase hero {hero} pos {pos}: {len(rows)} items")
            top = sorted(rows.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
            lines.append("  sample: " + ", ".join(f"{names.get(i, ['?'])[0]} {m}/{w}" for i, (m, w) in top))
        lines.insert(0, "STRATZ check: OK")
    except StratzError as e:
        lines.insert(0, f"STRATZ check: FAILED: {e}")
    return lines
