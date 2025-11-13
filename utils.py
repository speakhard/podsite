import os
import re
import requests
import markdown
from urllib.parse import urlparse


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def to_slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "site"


def md_to_html(md: str) -> str:
    return markdown.markdown(
        md or "",
        extensions=["extra", "sane_lists", "smarty"],
    )


def download_image(url: str, dest: str) -> str:
    if not url:
        return ""
    ensure_dir(dest)
    name = os.path.basename(urlparse(url).path) or "image"
    if "." not in name:
        name += ".jpg"
    out_path = os.path.join(dest, name)
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return name
    except Exception:
        return ""
    return ""