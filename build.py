import os, sys, time, shutil, yaml, feedparser, datetime, glob, json, re
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
env.globals["now"] = lambda: datetime.datetime.utcnow()
env.filters["date"] = lambda dt, fmt="%b %d, %Y": datetime.datetime.fromtimestamp(dt).strftime(fmt) if isinstance(dt, (int, float)) else str(dt)

def render(tpl, ctx, out_path):
    ensure_dir(os.path.dirname(out_path))
    html = env.get_template(tpl).render(**ctx)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

def copy_static(dest): shutil.copytree(STATIC_DIR, os.path.join(dest, "static"), dirs_exist_ok=True)

def load_posts(slug):
    root = os.path.join(BASE_DIR, "content", "posts", slug)
    items = []
    if os.path.isdir(root):
        for p in sorted(glob.glob(os.path.join(root, "*.md")), reverse=True):
            with open(p, "r", encoding="utf-8") as f: raw = f.read()
            m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, flags=re.S)
            body, title, date_display = raw, os.path.splitext(os.path.basename(p))[0].replace("-"," ").title(), ""
            if m:
                fm, body = m.groups()
                for line in fm.splitlines():
                    if ":" in line:
                        k,v = [x.strip() for x in line.split(":",1)]
                        if k.lower()=="title": title=v
                        if k.lower()=="date":  date_display=v
            items.append({
                "slug": to_slug(os.path.splitext(os.path.basename(p))[0]),
                "title": title, "date_display": date_display, "html": md_to_html(body)
            })
    return items

def build_show(show, out_root):
    print(f"==> Building {show.get('title')}")
    fp = feedparser.parse(show["feed"])

    theme = show.get("theme", {})
    site = {
        "title": show.get("title") or fp.feed.get("title","Podcast"),
        "tagline": show.get("description") or fp.feed.get("subtitle") or fp.feed.get("description",""),
        "image": "",
        "analytics_head": "",
        # theming
        "accent": theme.get("accent", "#4b6bfb"),
        "bg": theme.get("bg", "#0d1117"),
        "panel": theme.get("panel", "#1f2937"),
        "header_bg": theme.get("header_bg", "#111827"),
        "text": theme.get("text", "#f4f4f4"),
        "muted": theme.get("muted", "#9ca3af"),
        "hero_title": theme.get("hero_title",""),
        "hero_blurb": theme.get("hero_blurb",""),
        "layout": theme.get("layout","cards"),
    }

    # artwork
    img_url = show.get("image")
    if not img_url and hasattr(fp.feed, "image") and isinstance(fp.feed.image, dict):
        img_url = fp.feed.image.get("href","")
    if img_url:
        saved = download_image(img_url, os.path.join(out_root,"images"))
        site["image"] = f"/images/{saved}" if saved else ""

    # analytics
    analytics = show.get("analytics",{}) or {}
    if analytics.get("plausible_domain"):
        dom = analytics["plausible_domain"]
        site["analytics_head"] += f'<script defer data-domain="{dom}" src="https://plausible.io/js/script.js"></script>\n'
    site["analytics_head"] += analytics.get("custom_head_html","")

    # CNAME
    if show.get("domain"):
        ensure_dir(out_root)
        with open(os.path.join(out_root, "CNAME"), "w") as f: f.write(show["domain"].strip())

    # episodes
    episodes=[]
    for e in fp.entries:
        published = None
        if getattr(e, "published_parsed", None): published = time.mktime(e.published_parsed)
        elif getattr(e, "updated_parsed", None): published = time.mktime(e.updated_parsed)
        else: published = time.time()

        audio=""
        if getattr(e, "enclosures", None): audio = e.enclosures[0].get("url","")
        elif getattr(e, "links", None):
            for L in e.links:
                if L.get("type","").startswith("audio"):
                    audio=L.get("href",""); break

        episodes.append({
            "id": e.get("id") or e.get("guid") or e.get("link") or str(published),
            "title": e.get("title","Untitled Episode"),
            "slug": to_slug(e.get("title") or str(published)),
            "link": e.get("link",""),
            "published": published,
            "summary": e.get("summary",""),
            "content": (getattr(e,"content",[{}])[0].get("value","") if hasattr(e,"content") else e.get("summary","")),
            "audio": audio,
            "transcript_url": getattr(e, "podcast_transcript", None) or ""
        })
    episodes.sort(key=lambda x: x["published"], reverse=True)

    posts = load_posts(show.get("slug") or to_slug(site["title"]))

    ctx = {"site":site, "show":show, "episodes":episodes, "posts":posts}

    copy_static(out_root)
    render("home.html", ctx, os.path.join(out_root,"index.html"))
    render("about.html", ctx, os.path.join(out_root,"about","index.html"))
    render("reviews.html", ctx, os.path.join(out_root,"reviews","index.html"))
    render("blog/index.html", ctx, os.path.join(out_root,"blog","index.html"))
    for ep in episodes:
        render("episode.html", {**ctx,"ep":ep}, os.path.join(out_root,"episodes",ep["slug"],"index.html"))

def main():
    target = os.getenv("SHOW_SLUG")  # build a single show if set
    with open(os.path.join(BASE_DIR,"config","shows.yaml"),"r") as f:
        cfg = yaml.safe_load(f)
    ensure_dir(PUBLIC_DIR)
    for show in cfg.get("shows",[]):
        slug = show.get("slug") or to_slug(show.get("title") or "podcast")
        if target and slug != target: continue
        build_show(show, os.path.join(PUBLIC_DIR, slug))
    print(f"\nAll done. Static sites generated in: {PUBLIC_DIR}")

if __name__=="__main__": main()