"""
Shared logic for the Draft Helper mod.

Downloads hero matchup data from dotapicker.com, caches it next to the mod and
renders it into a Panorama script (`minify_draft_helper_data.js`) that the
in-game draft panel reads. Every network failure is swallowed: the mod falls
back to the cached copy, and with no cache it ships an empty dataset so the
panel simply reports that no data is available.
"""

import json
import os
import time

DOTAPICKER_BASE = "https://dotapicker.com/assets/json/data/"
DOTAPICKER_FILES = ("heroinfo", "heroadvscores", "herowinscores", "herotimeadv")
USER_AGENT = "dota2-minify (Draft Helper mod)"

# Rank setting -> (dotapicker "skilllevels" index, STRATZ RankBracketBasicEnum).
# dotapicker only has Very High / High / Normal. The old setting values are kept for configs saved before.
BRACKETS = {
    "Herald-Guardian": (2, "HERALD_GUARDIAN"),
    "Crusader-Archon": (2, "CRUSADER_ARCHON"),
    "Legend-Ancient": (1, "LEGEND_ANCIENT"),
    "Divine-Immortal": (0, "DIVINE_IMMORTAL"),
    "Very High": (0, "DIVINE_IMMORTAL"),
    "High": (1, "LEGEND_ANCIENT"),
    "Normal": (2, "CRUSADER_ARCHON"),
}
DEFAULT_BRACKET = "Legend-Ancient"
HOTKEYS = ("None", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "Insert", "Home", "PageUp", "PageDown")

mod_dir = os.path.dirname(os.path.abspath(__file__))
cache_dir = os.path.join(mod_dir, "cache")
data_js_path = os.path.join(mod_dir, "files_uncompiled", "panorama", "scripts", "hud", "minify_draft_helper_data.js")


def _cache_file(name):
    return os.path.join(cache_dir, f"{name}.json")


def fetch_dotapicker(timeout=15):
    """
    Downloads all dotapicker files into the cache directory.
    Returns True only if every file was fetched and parsed; failures are silent.
    """
    try:
        import requests
    except Exception:
        return False

    fetched = {}
    for name in DOTAPICKER_FILES:
        try:
            response = requests.get(
                f"{DOTAPICKER_BASE}{name}.json?cv={int(time.time())}",
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            if response.status_code != 200:
                return False
            fetched[name] = response.json()
        except Exception:
            return False

    if not _looks_valid(fetched):
        return False

    try:
        os.makedirs(cache_dir, exist_ok=True)
        for name, payload in fetched.items():
            with open(_cache_file(name), "w", encoding="utf-8") as file:
                json.dump(payload, file)
    except Exception:
        return False
    return True


def load_cached():
    raw = {}
    for name in DOTAPICKER_FILES:
        try:
            with open(_cache_file(name), encoding="utf-8") as file:
                raw[name] = json.load(file)
        except Exception:
            return None
    return raw if _looks_valid(raw) else None


def _looks_valid(raw):
    try:
        names = raw["heroinfo"]["heronames"]
        return (
            len(names) > 100
            and raw["heroadvscores"]["heronames"] == names
            and raw["herowinscores"]["heronames"] == names
            and len(raw["heroadvscores"]["adv_rates"]) == len(names)
            and len(raw["herotimeadv"]["timeadv"]) == 3
        )
    except Exception:
        return False


def _cell(matrix, row, col, bracket):
    """Returns matrix[row][col][bracket] as a float or None."""
    try:
        value = matrix[row][col]
        if value is None:
            return None
        value = value[bracket]
        return None if value is None else float(value)
    except Exception:
        return None


def _flat(matrix, n, bracket, negate_transposed):
    """
    Flattens an n*n dotapicker matrix into ints (value * 10) indexed [candidate * n + other].

    dotapicker stores `matrix[other][candidate]` as the *other* hero's perspective. For
    the enemy matrices the candidate's value is the negation (same as the site's
    `-enemy.advantage[candidate]`); for the friend matrices it is used as is.
    """
    out = []
    for cand in range(n):
        for other in range(n):
            value = _cell(matrix, other, cand, bracket)
            if value is None:
                out.append(0)
                continue
            if negate_transposed:
                value = -value
            out.append(int(round(value * 10)))
    return out


def _npc_name(name, vnames):
    if name in vnames:
        return "npc_dota_hero_" + vnames[name]
    clean = name.lower().replace("-", "").replace("'", "").replace(" ", "_")
    return "npc_dota_hero_" + clean


def build_dataset(raw, bracket):
    info = raw["heroinfo"]
    adv = raw["heroadvscores"]
    win = raw["herowinscores"]
    timeadv = raw["herotimeadv"]["timeadv"][bracket]

    names = info["heronames"]
    n = len(names)
    vnames = info.get("vnames", {})
    heroes = []
    for name in names:
        desc = info.get("description", {}).get(name, {})
        stages = timeadv.get(name, {})
        early, mid, late = stages.get("hearly"), stages.get("hmid"), stages.get("hlate")
        if None not in (early, mid, late):
            avg = (early + mid + late) / 3
            stage = [round((early - avg) * 100, 1), round((mid - avg) * 100, 1), round((late - avg) * 100, 1)]
        else:
            stage = [0, 0, 0]
        heroes.append(
            {
                "id": int(info.get("valveids", {}).get(name, 0)),
                "name": name,
                "npc": _npc_name(name, vnames),
                "roles": desc.get("roles", []),
                "canRoles": desc.get("canRoles", []),
                "wr": round(float(stages.get("pfull", 0.5)) * 100, 1),
                "stage": stage,
            }
        )

    return {
        "source": "dotapicker.com",
        "patch": str(adv.get("map_version", "")),
        "updated": int(adv.get("last_update", 0)),
        "bracket": bracket,
        "n": n,
        "heroes": heroes,
        # candidate advantage vs enemy (dotapicker "advantage"), x10
        "adv": _flat(adv["adv_rates"], n, bracket, negate_transposed=True),
        # candidate synergy with ally (dotapicker "synergy"), x10
        "syn": _flat(adv["adv_rates_friends"], n, bracket, negate_transposed=False),
        # winrate-based counterparts (dotapicker "winrate" mode), x10
        "wvs": _flat(win["win_rates"], n, bracket, negate_transposed=True),
        "wwith": _flat(win["win_rates_friends"], n, bracket, negate_transposed=False),
    }


def build_config(settings):
    bracket_name = settings.get("bracket", DEFAULT_BRACKET)
    if bracket_name not in BRACKETS:
        bracket_name = DEFAULT_BRACKET
    hotkey = settings.get("hotkey", "F8")
    try:
        size = int(settings.get("default_size", 3))
    except (TypeError, ValueError):
        size = 3
    return {
        "bracket": BRACKETS[bracket_name][0],
        "stratzBracket": BRACKETS[bracket_name][1],
        "bracketName": bracket_name,
        "hotkey": hotkey if hotkey in HOTKEYS else "None",
        "globalBind": bool(settings.get("global_bind", False)),
        "size": max(1, min(5, size)),
        "side": "left" if settings.get("panel_side") == "Left" else "right",
        "debug": bool(settings.get("debug", False)),
        "autoPosition": bool(settings.get("auto_position", True)),
    }


def write_data_js(dataset, config, path=data_js_path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write('"use strict";\n')
        file.write("// Generated by Minify (Draft Helper). Do not edit, it is rewritten on every patch.\n")
        file.write("var MDH_CONFIG = " + json.dumps(config, separators=(",", ":")) + ";\n")
        file.write("var MDH_DATA = " + json.dumps(dataset, separators=(",", ":"), ensure_ascii=False) + ";\n")


def generate(settings, refresh=True, force_stratz=False, log=None):
    """
    Entry point used by script.py / script_utility.py.
    Returns (status, stratz_status, dataset); statuses are "fresh", "cached" or "missing".
    STRATZ data is optional: it needs a token and is cached for 12 hours.
    """
    import draft_helper_stratz as stratz

    config = build_config(settings)
    errors = []

    def remember(message):
        errors.append(message)
        if log:
            log(message)

    status = "fresh" if refresh and fetch_dotapicker() else "cached"
    raw = load_cached()
    dataset = None
    if raw is not None:
        try:
            dataset = build_dataset(raw, config["bracket"])
        except Exception:
            dataset = None
    if dataset is None:
        status = "missing"

    stratz_status = "missing"
    if dataset is not None:
        try:
            hero_ids = [h["id"] for h in dataset["heroes"] if h["id"]]
            token = stratz.load_token()
            config["stratzToken"] = bool(token)
            stratz_status, stratz_raw = stratz.refresh(
                token, config["stratzBracket"], hero_ids, cache_dir, force=force_stratz, log=remember
            )
            if stratz_raw:
                dataset["stratz"] = stratz.build_section(stratz_raw, dataset["heroes"])
                dataset["dpScale"] = {
                    "adv": round(_std_flat(dataset["adv"]), 3),
                    "syn": round(_std_flat(dataset["syn"]), 3),
                }
        except Exception as e:
            stratz_status = "missing"
            remember(f"[Draft Helper] STRATZ data skipped: {type(e).__name__}: {e}")

    if errors:
        # Shown in the in-game panel so problems are visible without digging through logs
        config["stratzError"] = errors[-1].replace("[Draft Helper] ", "")[:160]
    write_data_js(dataset, config)
    return status, stratz_status, dataset


def _std_flat(values):
    """Standard deviation of the non-zero values of a x10 matrix, in real units."""
    values = [v / 10 for v in values if v]
    if len(values) < 2:
        return 1.0
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5 or 1.0
