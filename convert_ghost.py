#!/usr/bin/env python3
"""Convert Ghost JSON export to Hugo markdown files."""

import json
import os
import re
import html2text
from datetime import datetime

EXPORT_PATH = "/Users/alberto/.hermes/cache/documents/doc_2239ffaa51e9_grageras-blog.ghost.2026-07-02-21-01-57.json"
CONTENT_DIR = "/Users/alberto/Boti/gragera.me/content"


def yaml_dump(data):
    """Simple YAML dump for frontmatter."""
    lines = []
    for key, val in data.items():
        if isinstance(val, str):
            escaped = val.replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        elif isinstance(val, list):
            items = '\n'.join(f'  - "{v}"' for v in val)
            lines.append(f'{key}:\n{items}')
        elif isinstance(val, bool):
            lines.append(f'{key}: {"true" if val else "false"}')
        else:
            lines.append(f'{key}: {val}')
    return '\n'.join(lines)


# Load export
with open(EXPORT_PATH) as f:
    data = json.load(f)

d = data['db'][0]['data']
posts = d['posts']
tags_map = {t['id']: t for t in d.get('tags', [])}

# Build post-tag mapping
post_tags = {}
for pt in d.get('posts_tags', []):
    pid = pt['post_id']
    tid = pt['tag_id']
    post_tags.setdefault(pid, []).append(tags_map[tid]['slug'])

h = html2text.HTML2Text()
h.body_width = 0
h.protect_links = True
h.ignore_links = False
h.ignore_images = False
h.escape_snob = True


def clean_ghost_url(html):
    """Replace __GHOST_URL__ references with site root."""
    return html.replace('__GHOST_URL__', '')


def post_type(slug, title, html):
    """Determine if this is a blog post, page, or app page."""
    if slug in ('mise', 'mise-privacy'):
        return 'page'
    if slug in ('about', 'about-me'):
        return 'page'
    return 'post'


published = [p for p in posts if p['status'] == 'published' and not p.get('page')]

print(f"Converting {len(published)} published items...")

for p in published:
    slug = p['slug']
    title = p['title']
    published_at = p.get('published_at', '')
    html_content = p.get('html') or ''
    mobiledoc = p.get('mobiledoc') or ''
    feature_image = p.get('feature_image', '') or ''
    excerpt = p.get('custom_excerpt', '') or p.get('excerpt', '') or ''

    # Clean Ghost URL references
    html_content = clean_ghost_url(html_content)
    feature_image = clean_ghost_url(feature_image)

    # Skip empty posts (like mise-2 which is a duplicate)
    if not html_content.strip() and not mobiledoc:
        print(f"  SKIP: {slug} — no content")
        continue

    # Parse date
    if published_at:
        dt = datetime.strptime(published_at.replace('Z', '+0000'), '%Y-%m-%dT%H:%M:%S.%f%z')
        date_str = dt.strftime('%Y-%m-%dT%H:%M:%S%z')
    else:
        date_str = ''

    # Get tags (filter out import tags)
    tags = [t for t in post_tags.get(p['id'], []) if not t.startswith('hash-import')]

    ptype = post_type(slug, title, html_content)
    section = 'posts' if ptype == 'post' else 'pages'

    # For blog posts with standard HTML, convert to markdown
    # For pages with embedded HTML (like Mise landing page), keep HTML
    uses_raw_html = html_content.strip().startswith(('<!--', '<nav', '<!DOCTYPE'))
    if ptype == 'post' and not uses_raw_html:
        body = h.handle(html_content)
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = body.strip()
    else:
        body = html_content.strip()

    # Build frontmatter
    frontmatter = {
        'title': title,
        'date': date_str,
        'draft': False,
    }
    if slug != os.path.basename(slug):
        frontmatter['slug'] = slug
    if tags:
        frontmatter['tags'] = tags
    if excerpt:
        frontmatter['description'] = excerpt
    if feature_image:
        frontmatter['featured_image'] = feature_image

    # Write file
    section_dir = os.path.join(CONTENT_DIR, section)
    os.makedirs(section_dir, exist_ok=True)

    if uses_raw_html:
        file_path = os.path.join(section_dir, f"{slug}.html")
    else:
        file_path = os.path.join(section_dir, f"{slug}.md")

    content = "---\n" + yaml_dump(frontmatter) + "\n---\n\n" + body + "\n"

    with open(file_path, 'w') as f:
        f.write(content)

    print(f"  OK: /{section}/{slug} ({ptype}, {len(body)} chars)")

print("\nDone!")
