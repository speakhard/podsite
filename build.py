import os
import time
import shutil
import yaml
import feedparser
import glob
import json
import re
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape
from utils import ensure_dir, to_slug, md_to_html, download_image

BASE_DIR = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.globals["now"] = lambda: datetime.now(timezone.utc)
env.filters["date"] = (
    lambda ts, fmt="%b %d, %Y": datetime.fromtimestamp(ts).strftime(fmt)
    if isinstance(ts, (int, float))
    else str(ts)
)


def render(template, context, out_path):
    html = env.get_template(template).render(**context)
    ensure_dir(os.path.dirname(out_path))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def copy_static(dest):
    shutil.copytree(STATIC_DIR, os.path.join(dest, "static"), dirs_exist_ok=True)


def load_posts(slug: str):
    posts_root = os.path.join(BASE_DIR, "content", "posts", slug)
    items = []
    if os.path.isdir(posts_root):
        for p in sorted(glob.glob(os.path.join(posts_root, "*.md")), reverse=True):
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()

            title = os.path.splitext(os.path.basename(p))[0].replace("-", " ").title()
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

            html = md_to_html(body)
            items.append(
                {
                    "slug": to_slug(os.path.splitext(os.path.basename(p))[0]),
                    "title": title,
                    "html": html,
                    "date_display": date_display,
                }
            )
    return items


def load_episode_extra(show_slug: str, ep_slug: str):
    """
    Optional per-episode overrides:
    content/episodes/<show-slug>/<episode-slug>.md

    ---
    hosts:
      - name: "Host Name"
        url: "https://example.com"
    guests:
      - name: "Guest Name"
        url: "https://example.com"
    comments:
      - "Note 1"
      - "Note 2"
    ---

    <markdown body for notes/transcript>
    """
    path = os.path.join(BASE_DIR, "content", "episodes", show_slug, f"{ep_slug}.md")
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, flags=re.S)
    front, body = {}, raw
    if m:
        fm, body = m.groups()
        front = yaml.safe_load(fm) or {}

    html_body = md_to_html(body)
    return {
        "hosts": front.get("hosts", []),
        "guests": front.get("guests", []),
        "comments": front.get("comments", []),
        "extra_html": html_body,
    }


def build_show(show, out_root):
    print(f"==> Building {show.get('title')}")
    fp = feedparser.parse(show["feed"])

    theme = show.get("theme", {}) or {}

    site = {
        "title": show.get("title") or fp.feed.get("title", "Podcast"),
        "tagline": show.get("description")
        or fp.feed.get("subtitle")
        or fp.feed.get("description", ""),
        "image": "",
        "analytics_head": "",
        # theming
        "accent": theme.get("accent", "#4b6bfb"),
        "bg": theme.get("bg", "#0d1117"),
        "panel": theme.get("panel", "#1f2937"),
        "header_bg": theme.get("header_bg", "#111827"),
        "text": theme.get("text", "#f4f4f4"),
        "muted": theme.get("muted", "#9ca3af"),
        "hero_title": theme.get("hero_title", ""),
        "hero_blurb": theme.get("hero_blurb", ""),
        "layout": theme.get("layout", "cards"),
    }

    # Show-wide artwork
    img_url = ""
    if hasattr(fp.feed, "image"):
        img = getattr(fp.feed, "image")
        if isinstance(img, dict):
            img_url = img.get("href", "") or ""
    img_url = show.get("image") or img_url

    images_dir = os.path.join(out_root, "images")
    if img_url:
        saved = download_image(img_url, images_dir)
        site["image"] = f"/images/{saved}" if saved else ""

    # Analytics
    analytics = show.get("analytics", {}) or {}
    if analytics.get("plausible_domain"):
        dom = analytics["plausible_domain"]
        site["analytics_head"] += (
            f'<script defer data-domain="{dom}" '
            f'src="https://plausible.io/js/script.js"></script>\n'
        )
    site["analytics_head"] += analytics.get("custom_head_html", "")

    # CNAME for custom domains
    if show.get("domain"):
        ensure_dir(out_root)
        with open(os.path.join(out_root, "CNAME"), "w") as f:
            f.write(show["domain"].strip())

    # Episodes
    episodes = []
    for e in fp.entries:
        # Published date
        if getattr(e, "published_parsed", None):
            published = time.mktime(e.published_parsed)
        elif getattr(e, "updated_parsed", None):
            published = time.mktime(e.updated_parsed)
        else:
            published = time.time()

        # Audio URL
        audio_url = ""
        if getattr(e, "enclosures", None):
            audio_url = e.enclosures[0].get("url", "")
        elif getattr(e, "links", None):
            for L in e.links:
                if L.get("type", "").startswith("audio"):
                    audio_url = L.get("href", "")
                    break

        # Base summary from feed
        raw_summary = e.get("summary", "")
        plain_summary = re.sub("<[^>]+>", "", raw_summary or "").strip()

        # Subtitle: prefer itunes fields, else truncated summary
        subtitle = getattr(e, "itunes_subtitle", None) or getattr(
            e, "itunes_summary", None
        )
        if not subtitle:
            subtitle = plain_summary
            if len(subtitle) > 180:
                subtitle = subtitle[:177] + "…"

        # Full content for episode page (HTML from feed, if any)
        full_content = (
            getattr(e, "content", [{}])[0].get("value", "")
            if hasattr(e, "content")
            else raw_summary
        )

        # Episode artwork via itunes:image or image; fallback to show art
        ep_image_url = ""
        ep_image_path = ""

        # itunes:image
        if hasattr(e, "itunes_image"):
            img = getattr(e, "itunes_image")
            if isinstance(img, dict):
                ep_image_url = img.get("href", "") or ""
            else:
                ep_image_url = str(img)

        # generic image
        if not ep_image_url and hasattr(e, "image"):
            img = getattr(e, "image")
            if isinstance(img, dict):
                ep_image_url = img.get("href", "") or ""
            else:
                ep_image_url = str(img)

        # download episode art if available
        if ep_image_url:
            saved_ep = download_image(ep_image_url, images_dir)
            if saved_ep:
                ep_image_path = f"/images/{saved_ep}"

        # fallback: ALWAYS at least show the show art if we have it
        if not ep_image_path and site.get("image"):
            ep_image_path = site["image"]

        ep = {
            "id": e.get("id") or e.get("guid") or e.get("link") or str(published),
            "title": e.get("title", "Untitled Episode"),
            "slug": to_slug(e.get("title") or str(published)),
            "link": e.get("link", ""),
            "published": published,

            # list/card display
            "subtitle": subtitle,
            "summary": subtitle,

            # long-form description for the episode page
            "description": plain_summary,
            "content": full_content,

            "audio": audio_url,
            "image": ep_image_path,
            "transcript_url": getattr(e, "podcast_transcript", None) or "",
        }
        episodes.append(ep)

    episodes.sort(key=lambda x: x["published"], reverse=True)

    # Per-episode extras from local markdown
    show_slug = show.get("slug") or to_slug(site["title"])
    default_hosts = show.get("hosts", []) or []

    for ep in episodes:
        extra = load_episode_extra(show_slug, ep["slug"])

        # hosts: per-episode hosts override or extend show-level hosts
        ep["hosts"] = extra.get("hosts", []) or default_hosts
        ep["guests"] = extra.get("guests", [])
        ep["comments"] = extra.get("comments", [])
        ep["extra_html"] = extra.get("extra_html", "")

    posts = load_posts(show_slug)

    context = {
        "site": site,
        "show": show,
        "episodes": episodes,
        "posts": posts,
    }

    copy_static(out_root)

    render("home.html", context, os.path.join(out_root, "index.html"))
    render("about.html", context, os.path.join(out_root, "about", "index.html"))
    render("reviews.html", context, os.path.join(out_root, "reviews", "index.html"))
    render("blog/index.html", context, os.path.join(out_root, "blog", "index.html"))
    for ep in episodes:
        render(
            "episode.html",
            {**context, "ep": ep},
            os.path.join(out_root, "episodes", ep["slug"], "index.html"),
        )

    print(f"Finished {site['title']} -> {out_root}")


def main():
    target = os.getenv("SHOW_SLUG")  # build just one show if set
    with open(os.path.join(BASE_DIR, "config", "shows.yaml"), "r") as f:
        config = yaml.safe_load(f)

    ensure_dir(PUBLIC_DIR)
    for show in config.get("shows", []):
        slug = show.get("slug") or to_slug(show.get("title") or "podcast")
        if target and slug != target:
            continue
        out_root = os.path.join(PUBLIC_DIR, slug)
        build_show(show, out_root)

    print(f"\nAll done. Static sites generated in: {PUBLIC_DIR}")


if __name__ == "__main__":
    main()
