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

import draft_helper_core
import draft_helper_stratz as stratz

# Minify keeps imported modules for its whole session; reload so an updated mod is used without a restart
importlib.reload(stratz)
importlib.reload(draft_helper_core)
from core import config, log, output
from patch import manifest_utils

mod_name = os.path.basename(current_dir)


def get_settings():
    return manifest_utils.get_effective_settings(manifest_utils.get_mod(current_dir), config.get_mod(mod_name))


def move_token_out_of_config(settings):
    # The token is kept in a private file next to the mod, not in Minify's main config
    try:
        if stratz.store_token(settings.get("stratz_token")):
            mod_cfg = config.get_mod(mod_name)
            mod_cfg["stratz_token"] = ""
            config.set_mod(mod_name, mod_cfg)
    except Exception:
        pass


def main():
    settings = get_settings()
    move_token_out_of_config(settings)
    try:
        status, stratz_status, dataset = draft_helper_core.generate(settings, refresh=True, log=log.write_warning)
    except Exception as e:
        # Never break the patch because of this mod
        log.write_warning(f"[Draft Helper] failed to prepare data: {e}")
        return

    if status == "fresh":
        output.add_text(f"Draft Helper: dotapicker {dataset['patch']} data updated", msg_type="success", indent=True)
    elif status == "cached":
        output.add_text("Draft Helper: dotapicker unreachable, using cached data", msg_type="warning", indent=True)
    else:
        output.add_text("Draft Helper: no hero data available (offline?)", msg_type="warning", indent=True)

    if stratz_status == "fresh":
        output.add_text("Draft Helper: STRATZ data updated", msg_type="success", indent=True)
    elif stratz_status == "cached":
        output.add_text("Draft Helper: STRATZ data from cache (refreshed every 12h)", indent=True)
    elif stratz.load_token():
        output.add_text("Draft Helper: STRATZ data unavailable, see logs/warnings.txt", msg_type="warning", indent=True)


if __name__ == "__main__":
    main()
