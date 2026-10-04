#!/usr/bin/env python3
"""Build one static page per business from YAML files.

Layout expected next to this script:

    site.yaml               shared settings (base_url, pricing, newsletter links, common FAQ)
    businesses/*.yaml       one file per business
    templates/              page.html.j2, llms.txt.j2, index.html.j2, index.llms.txt.j2

Output (default: dist/):

    dist/index.html                 home page           -> <base_url>
    dist/llms.txt                   catalogue of every service, for AI agents
    dist/tools.json                 every tool definition in one file
    dist/<slug>/index.html          the business page   -> <base_url><slug>/
    dist/<slug>/llms.txt            plain-text summary for AI agents
    dist/<slug>/tools.json          MCP-style tool definition with example (mockup)

dist/ is published as-is to GitHub Pages (see .github/workflows/pages.yml).
Links between pages are relative; canonical URLs use base_url from site.yaml.

Usage:
    uv sync
    uv run build.py                 # links like ../<slug>/, for serving over HTTP
    uv run build.py --local         # links like ../<slug>/index.html, for opening from disk
    uv run build.py --only humans   # build a single page
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent

REQUIRED_KEYS = [
    "slug", "name", "accent", "tagline", "audience", "problem",
    "how_it_works", "benefits", "audiences", "demo", "faq",
]
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")  # lowercase URL-safe slug
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- loading

def load_yaml(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise BuildError(f"{path.name}: invalid YAML\n{e}") from None
    if not isinstance(data, dict):
        raise BuildError(f"{path.name}: expected a mapping at the top level")
    return data


def load_businesses(folder: Path) -> list[dict]:
    files = sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml"))
    if not files:
        raise BuildError(f"No YAML files found in {folder}")
    businesses = []
    for path in files:
        b = load_yaml(path)
        b["_file"] = path.name
        businesses.append(b)
    businesses.sort(key=lambda b: (b.get("order", 999), b.get("slug", "")))
    return businesses


# ---------------------------------------------------------------- checks

def validate(businesses: list[dict]) -> None:
    errors: list[str] = []
    slugs = [b.get("slug") for b in businesses]

    for b in businesses:
        where = b["_file"]
        missing = [k for k in REQUIRED_KEYS if not b.get(k)]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue

        if not SLUG_RE.match(str(b["slug"])):
            errors.append(f"{where}: slug '{b['slug']}' is not a valid URL path segment")
        if slugs.count(b["slug"]) > 1:
            errors.append(f"{where}: slug '{b['slug']}' is used more than once")
        for key in ("accent", "accent_dark"):
            if b.get(key) and not HEX_RE.match(b[key]):
                errors.append(f"{where}: {key} must be a hex colour like #1a2b3c")

        for r in b.get("related", []):
            if r not in slugs:
                errors.append(f"{where}: related slug '{r}' does not exist")

        tool = b["demo"].get("tool", {})
        for k in ("name", "description", "inputs", "returns"):
            if not tool.get(k):
                errors.append(f"{where}: demo.tool.{k} is missing")
        inputs = {i["name"]: i for i in tool.get("inputs", [])}
        request = b["demo"].get("example_request") or {}
        unknown = set(request) - set(inputs)
        if unknown:
            errors.append(f"{where}: example_request uses undeclared inputs: {', '.join(sorted(unknown))}")
        absent = [n for n, i in inputs.items() if i.get("required") and n not in request]
        if absent:
            errors.append(f"{where}: example_request is missing required inputs: {', '.join(absent)}")
        if "example_response" not in b["demo"]:
            errors.append(f"{where}: demo.example_response is missing")

    if errors:
        raise BuildError("\n".join(errors))


def validate_groups(site: dict, businesses: list[dict]) -> None:
    slugs = {b["slug"] for b in businesses}
    seen: list[str] = [s for g in site["home"]["groups"] for s in g["slugs"]]
    errors = [f"site.yaml: home.groups lists unknown slug '{s}'" for s in seen if s not in slugs]
    errors += [f"site.yaml: home.groups lists '{s}' more than once" for s in set(seen) if seen.count(s) > 1]
    errors += [f"site.yaml: home.groups is missing '{s}'" for s in sorted(slugs - set(seen))]
    if errors:
        raise BuildError("\n".join(errors))


# ---------------------------------------------------------------- helpers

def paragraphs(text: str) -> list[str]:
    """Split a YAML block string into paragraphs on blank lines."""
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def signature(tool: dict) -> str:
    args = [i["name"] if i.get("required") else f"{i['name']}?" for i in tool["inputs"]]
    return f"{tool['name']}({', '.join(args)})"


def pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def tools_json(b: dict) -> dict:
    return {"x-status": "static mockup, not a live MCP server", "tools": [tool_def(b)]}


def tool_def(b: dict) -> dict:
    tool = b["demo"]["tool"]
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": {
            "type": "object",
            "properties": {
                i["name"]: {"type": i["type"], "description": i["description"]}
                for i in tool["inputs"]
            },
            "required": [i["name"] for i in tool["inputs"] if i.get("required")],
        },
        "x-returns": tool["returns"],
        "x-example": {
            "arguments": b["demo"]["example_request"],
            "result": b["demo"]["example_response"],
        },
    }


def json_ld(b: dict, site: dict, page_url: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Service",
        "name": b["name"],
        "description": b["tagline"],
        "url": page_url,
        "audience": {"@type": "Audience", "audienceType": b["audience"]},
        "provider": {"@type": "Organization", "name": site["family_name"]},
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR",
                   "description": site["pricing"]["headline"]},
    }


# ---------------------------------------------------------------- build

def build(args: argparse.Namespace) -> None:
    site = load_yaml(args.site)
    businesses = load_businesses(args.businesses)
    validate(businesses)
    validate_groups(site, businesses)

    base_url = site["base_url"].rstrip("/") + "/"
    suffix = "index.html" if args.local else ""

    def url_for(slug: str, prefix: str = "../") -> str:
        return f"{prefix}{slug}/{suffix}"

    index_url_from_page = f"../{suffix}"

    env = Environment(
        loader=FileSystemLoader(args.templates),
        autoescape=lambda name: bool(name) and name.endswith(".html.j2"),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["paragraphs"] = paragraphs
    page_tpl = env.get_template("page.html.j2")
    llms_tpl = env.get_template("llms.txt.j2")
    index_tpl = env.get_template("index.html.j2")
    index_llms_tpl = env.get_template("index.llms.txt.j2")

    by_slug = {b["slug"]: b for b in businesses}
    siblings = [
        {"slug": b["slug"], "name": b["name"], "tagline": b["tagline"], "audience": b["audience"],
         "accent": b["accent"], "accent_dark": b.get("accent_dark") or b["accent"],
         "tool": b["demo"]["tool"]["name"], "description": b["demo"]["tool"]["description"],
         "signature": signature(b["demo"]["tool"]), "url": url_for(b["slug"]),
         "page_url": f"{base_url}{b['slug']}/"}
        for b in businesses
    ]

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    selected = [b for b in businesses if not args.only or b["slug"] in args.only]
    if args.only and not selected:
        raise BuildError(f"No business matches --only {' '.join(args.only)}")

    for b in selected:
        b.setdefault("status", site.get("status_default", "Concept"))
        b.setdefault("accent_dark", None)
        b["demo"].setdefault("intro", "")
        page_url = f"{base_url}{b['slug']}/"
        ctx = {
            "site": site,
            "b": b,
            "page_url": page_url,
            "index_url": index_url_from_page,
            "signature": signature(b["demo"]["tool"]),
            "example_request": pretty({"name": b["demo"]["tool"]["name"],
                                       "arguments": b["demo"]["example_request"]}),
            "example_response": pretty(b["demo"]["example_response"]),
            "pricing": b.get("pricing", site["pricing"]),
            "newsletter": b.get("newsletter", site["newsletter"]),
            "faq": list(b["faq"]) + list(site.get("common_faq", [])),
            "siblings": siblings,
            "related": [{"name": by_slug[r]["name"], "url": url_for(r)} for r in b.get("related", [])],
            "json_ld": json_ld(b, site, page_url),
        }
        folder = out / b["slug"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(page_tpl.render(ctx), encoding="utf-8")
        (folder / "llms.txt").write_text(llms_tpl.render(ctx), encoding="utf-8")
        (folder / "tools.json").write_text(pretty(tools_json(b)) + "\n", encoding="utf-8")
        print(f"built {b['slug']:<12} -> {folder.relative_to(out.parent) if out.parent in folder.parents else folder}")

    root = {s["slug"]: dict(s, url=url_for(s["slug"], prefix="")) for s in siblings}
    groups = [{"title": g["title"], "services": [root[s] for s in g["slugs"]]}
              for g in site["home"]["groups"]]
    home_ctx = {
        "site": site, "home": site["home"], "groups": groups, "base_url": base_url,
        "count": len(businesses),
        "faq": list(site["home"].get("faq", [])) + list(site.get("common_faq", [])),
        "json_ld": {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": site["family_name"],
            "description": site["family_tagline"],
            "url": base_url,
            "makesOffer": [
                {"@type": "Offer", "price": "0", "priceCurrency": "EUR",
                 "itemOffered": {"@type": "Service", "name": s["name"],
                                 "description": s["tagline"], "url": s["page_url"]}}
                for s in siblings
            ],
        },
    }
    (out / "index.html").write_text(index_tpl.render(home_ctx), encoding="utf-8")
    (out / "llms.txt").write_text(index_llms_tpl.render(home_ctx), encoding="utf-8")
    (out / "tools.json").write_text(pretty({
        "x-status": "static mockup, not a live MCP server",
        "tools": [dict(tool_def(b), **{"x-page": f"{base_url}{b['slug']}/"}) for b in businesses],
    }) + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built index        -> {out / 'index.html'}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--site", type=Path, default=ROOT / "site.yaml")
    p.add_argument("--businesses", type=Path, default=ROOT / "businesses")
    p.add_argument("--templates", type=Path, default=ROOT / "templates")
    p.add_argument("--out", type=Path, default=ROOT / "dist")
    p.add_argument("--local", action="store_true",
                   help="link sibling pages as folders, for previewing from disk")
    p.add_argument("--only", nargs="+", metavar="SLUG", help="build only these slugs")
    args = p.parse_args()
    try:
        build(args)
    except BuildError as e:
        print(f"Build failed:\n{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
