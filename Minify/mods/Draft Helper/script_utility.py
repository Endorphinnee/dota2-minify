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
from core import config, output
from patch import manifest_utils

mod_name = os.path.basename(current_dir)


def fetch_draft_data():
    settings = manifest_utils.get_effective_settings(manifest_utils.get_mod(current_dir), config.get_mod(mod_name))
    try:
        status, dataset = draft_helper_core.generate(settings, refresh=True)
    except Exception:
        status, dataset = "missing", None

    if status == "fresh":
        output.add_text(
            f"Draft Helper: dotapicker {dataset['patch']} data downloaded. Patch again to apply it.", msg_type="success"
        )
    elif status == "cached":
        output.add_text("Draft Helper: dotapicker unreachable, kept cached data", msg_type="warning")
    else:
        output.add_text("Draft Helper: could not download dotapicker data (offline?)", msg_type="error")
