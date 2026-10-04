# TheMultiSpace

Static site for [themultispace.github.io](https://themultispace.github.io/): one page per
service the agent economy is missing.

## Layout

| Path | What it is |
| --- | --- |
| `site.yaml` | Shared settings: base URL, pricing, newsletter links, common FAQ |
| `businesses/*.yaml` | One file per service (name, pitch, demo tool, FAQ) |
| `templates/` | Jinja templates for the pages, `llms.txt` and the index |
| `build.py` | Generates `dist/` from the above |
| `pyproject.toml`, `uv.lock` | Dependencies, managed with [uv](https://docs.astral.sh/uv/) |

Each service is published at `https://themultispace.github.io/<slug>/`, together with
`llms.txt` (plain-text summary for AI agents) and `tools.json` (MCP-style tool definition, mockup).

## Build locally

```sh
uv run build.py                          # installs deps on first run, writes dist/
uv run -m http.server -d dist 8000       # preview at http://localhost:8000/
```

`uv run build.py --local` makes links point at `index.html` files so `dist/` can be opened
straight from disk. `uv run build.py --only humans` rebuilds a single page.

## Deploy

Pushing to `main` runs `.github/workflows/pages.yml`, which builds the site and publishes
`dist/` to GitHub Pages. In the repository settings, set **Pages → Build and deployment →
Source** to **GitHub Actions** (one-time).

## Adding a service

Copy a file in `businesses/`, change `slug`, `order` and the content, then build. The build
checks required fields, slug format, colours, `related` slugs and that the demo example matches
the tool's declared inputs.
