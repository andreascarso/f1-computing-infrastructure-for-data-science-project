"""Render docs/architecture.svg to docs/architecture.png (issue #24).

    python3 docs/render_architecture.py

Uses a headless Chrome or Chromium (no Python packages); the PNG is drawn at
twice the SVG's size, so it stays sharp on slides.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
SVG, PNG = DOCS / "architecture.svg", DOCS / "architecture.png"
CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "google-chrome", "chromium", "chromium-browser"]


def main():
    chrome = next((c for c in CHROMES if Path(c).exists() or shutil.which(c)), None)
    if not chrome:
        sys.exit("No Chrome or Chromium found")
    width, height = map(int, re.search(r'viewBox="0 0 (\d+) (\d+)"', SVG.read_text()).groups())
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={profile}", "--force-device-scale-factor=2",
                        f"--window-size={width},{height}", f"--screenshot={PNG}", SVG.as_uri()],
                       check=True, capture_output=True)
    print(f"Wrote {PNG.relative_to(DOCS.parent)} ({2 * width} x {2 * height} px)")


if __name__ == "__main__":
    main()
