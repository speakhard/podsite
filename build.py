import os
import time
import shutil
import yaml
import feedparser
import datetime
import json
import re
import html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from utils import ensure_dir, to_slug, download_image, md_to_html

# --------------------------------------------------------------------
# Paths & Jinja environment
# --------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
PUBLIC_DIR = BASE_DIR / "public"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

env.globals["now"] = lambda: datetime.datetime.now(datetime.timezone.utc)

env.filters["date"] = (
    lambda dt, fmt="%b %d, %Y": datetime.datetime.fromtimestamp(dt).strftime(fmt)
    if isinstance(dt, (int, float))
    else str(dt)
)

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def render(template: str, context: dict, out_path: Path):
    html_str = env.get_template(template).render(**context)
    ensure_dir(out_path.parent)
    out_path.write_text(html_str, encoding="utf-8")


def copy_static(dest: Path):
    """Copy static assets into <out_root>/static"""
    src = str(STATIC_DIR)
    dst = str(dest / "static")
    shutil.copytree(src, dst, dirs_exist_ok=True)


def load_reviews(slug: str):
    path = BASE_DIR / "data" / "reviews" / f"{slug}.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            return []
    return []


def load_posts(slug: str):
    posts_root = BASE_DIR / "content" / "posts" / slug
    items = []
    if posts_root.is_dir():
        for p in sorted(posts_root.glob("*.md"), reverse=True):
            raw = p.read_text(encoding="utf-8")
            title = p.stem.replace("-", " ").title()
            date_display = ""

            m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, flags=re.S)
            body = raw
            if m:
                fm, body = m.groups()
                for line in fm.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "title":
                            title = v
                        if k == "date":
                            date_display = v

            html_body = md_to_html(body)
            excerpt = re.sub(r"<[^>]+?>", "", html_body)[:180]
            if len(html_body) > 180:
                excerpt += "…"

            items.append(
                {
                    "slug": to_slug(p.stem),
                    "title": title,
                    "html": html_body,
                    "date_display": date_display,
                    "excerpt": excerpt,
                }
            )
    return items


def load_episode_page(show_slug: str, ep_slug: str):
    """
    Optional per-episode markdown:

    content/episodes/<show_slug>/<ep_slug>.md

    ---
    subtitle: "Short tagline for this episode"
    hosts:
      - name: "Host 1"
        url: "https://example.com"
    guests:
      - name: "Guest Name"
        url: "https://guest-site"
    comments:
      - "Any notes / CWs here."
    ---

    Transcript text...
    """
    path = BASE_DIR / "content" / "episodes" / show_slug / f"{ep_slug}.md"
    if not path.exists():
        return {}

    raw = path.read_text(encoding="utf-8")
    fm_data = {}
    body = raw

    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, flags=re.S)
    if m:
        fm_raw, body = m.groups()
        try:
            fm_data = yaml.safe_load(fm_raw) or {}
        except Exception:
            fm_data = {}

    transcript_html = md_to_html(body) if body.strip() else ""

    return {
        "hosts": fm_data.get("hosts", []),
        "guests": fm_data.get("guests", []),
        "comments": fm_data.get("comments", []),
        "frontmatter": fm_data,
        "transcript_html": transcript_html,
        "raw_markdown": body,
    }


# --------------------------------------------------------------------
# Build a single show
# --------------------------------------------------------------------


def build_show(show: dict, out_root: Path):
    print(f"==> Building {show.get('title')}")

    feed_url = show["feed"]
    fp = feedparser.parse(feed_url)

    show_slug = show.get("slug") or to_slug(show.get("title") or "podcast")

    # Basic site metadata
    site = {
        "title": show.get("title") or fp.feed.get("title", "Podcast"),
        "hero_title": show.get("hero_title") or show.get("title") or fp.feed.get("title", "Podcast"),
        "tagline": show.get("description")
        or fp.feed.get("subtitle")
        or fp.feed.get("description", ""),
        "hero_blurb": show.get("hero_blurb")
        or show.get("description")
        or fp.feed.get("subtitle")
        or fp.feed.get("description", ""),
        "link": show.get("domain") or fp.feed.get("link", ""),
        "color": show.get("color") or "#111827",
        "subscribe": show.get("subscribe", {}),
        "social": show.get("social", {}),
        "image": "",
        "analytics_head": "",
        "slug": show_slug,
    }

    # Show artwork
    img_url = ""
    if hasattr(fp.feed, "image"):
        img = getattr(fp.feed, "image")
        if isinstance(img, dict):
            img_url = img.get("href") or img.get("url") or ""

    if not img_url and hasattr(fp.feed, "itunes_image"):
        it_img = getattr(fp.feed, "itunes_image")
        if isinstance(it_img, dict):
            img_url = it_img.get("href") or it_img.get("url") or ""

    img_url = show.get("image") or img_url

    images_dir = os.path.join(out_root, "images")
    if img_url:
        saved = download_image(img_url, images_dir)
        # path relative to the show's root (e.g., /last-best-hope/)
        site["image"] = f"images/{saved}" if saved else ""

    # Analytics
    analytics = show.get("analytics", {}) or {}
    if analytics.get("plausible_domain"):
        dom = analytics["plausible_domain"]
        site["analytics_head"] += (
            f'<script defer data-domain="{dom}" src="https://plausible.io/js/script.js"></script>\n'
        )
    site["analytics_head"] += analytics.get("custom_head_html", "")

    # CNAME
    if show.get("domain"):
        ensure_dir(out_root)
        (out_root / "CNAME").write_text(show["domain"].strip(), encoding="utf-8")

    # Episodes
    episodes = []
    for e in fp.entries:
        # Published timestamp
        if hasattr(e, "published_parsed") and e.published_parsed:
            published = time.mktime(e.published_parsed)
        elif hasattr(e, "updated_parsed") and e.updated_parsed:
            published = time.mktime(e.updated_parsed)
        else:
            published = time.time()

        # Slug first, so we can load markdown page
        ep_slug = to_slug(e.get("title") or str(published))

        # Optional per-episode Markdown metadata (subtitle, hosts, guests, transcript, etc.)
        page_meta = load_episode_page(show_slug, ep_slug)

        # Audio URL
        audio_url = ""
        if hasattr(e, "enclosures") and e.enclosures:
            audio_url = e.enclosures[0].get("url", "")
        elif hasattr(e, "links"):
            for L in e.links:
                if L.get("type", "").startswith("audio"):
                    audio_url = L.get("href", "")
                    break

        # Summary / subtitle
        summary_raw = e.get("summary", "") or ""
        summary_html = html.unescape(summary_raw)
        plain_summary = re.sub(r"<[^>]+?>", "", summary_html).strip()

        # Subtitle priority:
        # 1) subtitle from episode markdown front matter
        # 2) itunes:subtitle or subtitle from feed
        # 3) trimmed plain summary
        subtitle = None
        fm = page_meta.get("frontmatter", {}) or {}
        if fm.get("subtitle"):
            subtitle = fm["subtitle"]

        if not subtitle:
            subtitle = getattr(e, "itunes_subtitle", None) or getattr(e, "subtitle", None)

        if not subtitle:
            base = plain_summary
            max_len = 160
            if len(base) > max_len:
                subtitle = base[:max_len].rstrip() + "…"
            else:
                subtitle = base

        subtitle = html.unescape(subtitle)

        # Episode image
        ep_image = ""
        if hasattr(e, "image"):
            img = getattr(e, "image")
            if isinstance(img, dict):
                ep_image = img.get("href") or img.get("url") or ""
        if hasattr(e, "itunes_image") and not ep_image:
            it_img = getattr(e, "itunes_image")
            if isinstance(it_img, dict):
                ep_image = it_img.get("href") or it_img.get("url") or ""

        ep = {
            "id": e.get("id") or e.get("guid") or e.get("link") or str(published),
            "title": e.get("title", "Untitled Episode"),
            "link": e.get("link", ""),
            "published": published,
            "summary": summary_html,
            "subtitle": subtitle,
            "content": (
                getattr(e, "content", [{}])[0].get("value", "")
                if hasattr(e, "content")
                else summary_raw
            ),
            "audio": audio_url,
            "slug": ep_slug,
            "transcript_url": getattr(e, "podcast_transcript", None) or "",
            "image": ep_image or site["image"],
            "page": page_meta,
        }

        episodes.append(ep)

    episodes.sort(key=lambda x: x["published"], reverse=True)

    # Reviews + posts
    reviews = load_reviews(show_slug)
    posts = load_posts(show_slug)

    context = {
        "site": site,
        "episodes": episodes,
        "show": show,
        "build_time": datetime.datetime.now(datetime.timezone.utc),
    }

    copy_static(out_root)

    # Pages
    render("home.html", context, out_root / "index.html")
    render("about.html", context, out_root / "about" / "index.html")
    render("reviews.html", {**context, "reviews": reviews}, out_root / "reviews" / "index.html")
    render("blog/index.html", {**context, "posts": posts}, out_root / "blog" / "index.html")

    for ep in episodes:
        render(
            "episode.html",
            {**context, "ep": ep},
            out_root / "episodes" / ep["slug"] / "index.html",
        )

    for p in posts:
        render(
            "blog/post.html",
            {**context, "post": p},
            out_root / "blog" / p["slug"] / "index.html",
        )

    # Sitemap + robots
    render("sitemap.xml", context, out_root / "sitemap.xml")
    robots_txt = "User-agent: *\nAllow: /\n"
    (out_root / "robots.txt").write_text(robots_txt, encoding="utf-8")


# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------


def main():
    target = os.getenv("SHOW_SLUG")  # optional: build only one show
    config_path = BASE_DIR / "config" / "shows.yaml"

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    ensure_dir(PUBLIC_DIR)

    for show in config.get("shows", []):
        slug = show.get("slug") or to_slug(show.get("title") or "podcast")
        if target and slug != target:
            continue
        out_root = PUBLIC_DIR / slug
        build_show(show, out_root)

    print(f"\nAll done. Static sites generated in: {PUBLIC_DIR}")


if __name__ == "__main__":
    main()
