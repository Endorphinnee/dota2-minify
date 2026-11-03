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

import draft_helper_core
import stratz
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
