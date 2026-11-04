import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
minify_root = os.path.abspath(os.path.join(current_dir, os.pardir, os.pardir))
if os.getcwd() != minify_root:
    os.chdir(minify_root)

if minify_root not in sys.path:
    sys.path.insert(0, minify_root)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# isort: split

import importlib

import draft_helper_companion as companion
import draft_helper_core
import draft_helper_stratz as stratz

# Minify keeps imported modules for its whole session; reload so an updated mod is used without a restart
importlib.reload(stratz)
importlib.reload(draft_helper_core)
importlib.reload(companion)
from core import base, config, log, output
from patch import manifest_utils

mod_name = os.path.basename(current_dir)


def _settings():
    return manifest_utils.get_effective_settings(manifest_utils.get_mod(current_dir), config.get_mod(mod_name))


def move_token_out_of_config(settings):
    """Moves a token typed into the settings into the mod's private file and clears it from the config."""
    if not stratz.store_token(settings.get("stratz_token")):
        return False
    mod_cfg = config.get_mod(mod_name)
    mod_cfg["stratz_token"] = ""
    config.set_mod(mod_name, mod_cfg)
    return True


def fetch_draft_data():
    settings = _settings()
    move_token_out_of_config(settings)
    try:
        status, stratz_status, dataset = draft_helper_core.generate(
            settings, refresh=True, force_stratz=True, log=log.write_warning
        )
    except Exception as e:
        log.write_warning(f"[Draft Helper] failed to prepare data: {e}")
        status, stratz_status, dataset = "missing", "missing", None

    if status == "fresh":
        output.add_text(f"Draft Helper: dotapicker {dataset['patch']} data downloaded", msg_type="success")
    elif status == "cached":
        output.add_text("Draft Helper: dotapicker unreachable, kept cached data", msg_type="warning")
    else:
        output.add_text("Draft Helper: could not download dotapicker data (offline?)", msg_type="error")

    if stratz_status == "fresh":
        output.add_text("Draft Helper: STRATZ data downloaded", msg_type="success")
    elif stratz.load_token():
        output.add_text("Draft Helper: STRATZ download failed, see logs/warnings.txt", msg_type="warning")
    output.add_text("Draft Helper: patch again to put the new data into the game")


def check_stratz():
    """Runs every STRATZ query the mod uses on a small sample and writes logs/draft_helper_stratz.txt."""
    settings = _settings()
    if move_token_out_of_config(settings):
        output.add_text("Draft Helper: STRATZ token saved", msg_type="success")
    token = stratz.load_token()
    if not token:
        output.add_text("Draft Helper: enter your STRATZ token in the mod settings first", msg_type="error")
        return

    bracket = draft_helper_core.build_config(settings)["stratzBracket"]
    try:
        lines = stratz.check(token, bracket)
    except Exception as e:
        lines = [f"STRATZ check: FAILED: {type(e).__name__}: {e}"]

    lines += player_query_report(token)

    report_path = os.path.join(base.logs_dir, "draft_helper_stratz.txt")
    try:
        os.makedirs(base.logs_dir, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as file:
            file.write(f"bracket: {bracket}\n" + "\n".join(lines).replace(token, "<token>") + "\n")
    except Exception as e:
        log.write_warning(f"[Draft Helper] could not write STRATZ report: {e}")
    ok = lines[0].endswith("OK")
    output.add_text(
        f"Draft Helper: STRATZ check {'OK' if ok else 'failed'}, report: {report_path}",
        msg_type="success" if ok else "error",
    )
    if not ok:
        output.add_text(lines[0], msg_type="error")
        return

    output.add_text("Draft Helper: downloading STRATZ data (about a minute)...")
    try:
        _, stratz_status, _ = draft_helper_core.generate(
            settings, refresh=False, force_stratz=True, log=log.write_warning
        )
    except Exception as e:
        log.write_warning(f"[Draft Helper] STRATZ download failed: {type(e).__name__}: {e}")
        stratz_status = "missing"
    if stratz_status == "fresh":
        output.add_text("Draft Helper: STRATZ data downloaded. Patch to put it into the game.", msg_type="success")
    else:
        output.add_text("Draft Helper: STRATZ download failed, see logs/warnings.txt", msg_type="error")


def open_player_window():
    """Opens the always-on-top window with ping, loss and STRATZ profiles of the players in the current game."""
    settings = _settings()
    # 29000 is the port VConsole (-vconsole) uses; the console log works without any port
    port = companion.parse_port(settings.get("netcon_port")) or companion.DEFAULT_PORT
    move_token_out_of_config(settings)

    heroes = companion.hero_names()
    russian = str(config.get("locale") or "").lower().startswith(("ru", "uk"))
    try:
        token = stratz.load_token() if settings.get("use_stratz", True) else ""
        result = companion.open_window(port, token, heroes, russian=russian, log=log.write_warning)
    except Exception as e:
        log.write_warning(f"[Draft Helper] could not open the player window: {type(e).__name__}: {e}")
        output.add_text("Draft Helper: could not open the player window, see logs/warnings.txt", msg_type="error")
        return
    output.add_text(f"Draft Helper: player window {result} (console port {port})", msg_type="success")
    if not settings.get("player_info"):
        output.add_text(
            "Draft Helper: enable 'Player info window' and patch, otherwise heroes and profiles stay empty",
            msg_type="warning",
        )


def player_query_report(token):
    """Builds the player window's STRATZ query and runs it on the user's own account (Minify's Steam ID)."""
    import json

    lines = []
    try:
        query, notes = companion.build_player_query(token)
        lines.append("player query: " + query)
        lines += ["  " + note for note in notes]
        from core import steam

        own_id = companion.account_id(steam.current_steam_id)
        if not own_id:
            lines.append("  own account: unknown Steam ID, skipped")
            return lines
        data = stratz.query(token, query, {"id": own_id})
        player = data.get("player") or {}
        recent = companion.summarize_recent(player.get("matches"), own_id, companion.hero_names())
        lines.append(
            f"  own account: matches {player.get('matchCount')}, recent: {json.dumps(recent, ensure_ascii=False)}"
        )
    except Exception as e:
        lines.append(f"player query failed: {type(e).__name__}: {e}")
    return lines
