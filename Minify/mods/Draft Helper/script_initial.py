"""
Runs when Minify starts: opens the player info window automatically if it is enabled.
Minify runs "initial" scripts for every mod, enabled or not, so the mod state is checked here.
"""

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

from core import base, config, log, mods_shared
from patch import manifest_utils

mod_name = os.path.basename(current_dir)


def main():
    try:
        if base.HEADLESS or not mods_shared.get_state(mod_name):
            return
        settings = manifest_utils.get_effective_settings(manifest_utils.get_mod(current_dir), config.get_mod(mod_name))
        if not (settings.get("player_info") and settings.get("auto_open_player_window")):
            return

        import draft_helper_companion as companion
        import draft_helper_stratz as stratz

        importlib.reload(stratz)
        importlib.reload(companion)
        port = companion.parse_port(settings.get("netcon_port")) or companion.DEFAULT_PORT
        stratz.store_token(settings.get("stratz_token"))
        russian = str(config.get("locale") or "").lower().startswith(("ru", "uk"))
        token = stratz.load_token() if settings.get("use_stratz", True) else ""
        companion.open_when_minify_is_ready(port, token, companion.hero_names(), russian=russian, log=log.write_warning)
    except Exception as e:
        log.write_warning(f"[Draft Helper] player window autostart failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
