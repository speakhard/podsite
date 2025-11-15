import os
import yaml
import feedparser
from pathlib import Path
import sys

# Add project root to path so we can import utils.py
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from utils import to_slug

CONFIG_PATH = BASE_DIR / "config" / "shows.yaml"


def load_show(target_slug: str | None = None):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for show in config.get("shows", []):
        slug = show.get("slug") or to_slug(show.get("title") or "podcast")
        if target_slug is None or slug == target_slug:
            return slug, show

    raise SystemExit(f"No show found with slug={target_slug!r}")


TEMPLATE = """---
hosts:
  - name: "Josh Bernhard"
    url: "https://joshbernhard.com"
  - name: "John Drake"

guests:
  # - name: "Guest Name"
  #   url: "https://guest-website.example"

comments:
  - "Add any production notes or content warnings here."

---

## Transcript

(Transcript for **{title}** goes here.)

You can add links, timestamps, and headings:

- 00:00 – Intro
- 05:10 – Main discussion
- 54:30 – Listener mail
"""


def main():
    target_slug = os.getenv("SHOW_SLUG")  # optional: SHOW_SLUG=last-best-hope
    show_slug, show = load_show(target_slug)

    feed_url = show["feed"]
    print(f"Loading feed for '{show_slug}' from {feed_url} …")
    fp = feedparser.parse(feed_url)

    out_root = BASE_DIR / "content" / "episodes" / show_slug
    out_root.mkdir(parents=True, exist_ok=True)

    created, skipped = 0, 0

    for entry in fp.entries:
        title = entry.get("title", "Untitled Episode")
        ep_slug = to_slug(title)
        out_path = out_root / f"{ep_slug}.md"

        if out_path.exists():
            skipped += 1
            continue

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE.format(title=title))

        created += 1
        print(f"Created: {out_path.relative_to(BASE_DIR)}")

    print(f"\nDone. Created {created} new files, skipped {skipped} existing.")


if __name__ == "__main__":
    main()
