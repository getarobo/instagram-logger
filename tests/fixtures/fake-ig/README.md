# fake-IG fixture site

Static HTML that mirrors enough of Instagram's saved-posts structure to test
the content scripts (`saved-grid.ts`, `post-detail.ts`) without hitting real Instagram.

## Structure

```
fake-ig/
├── you/
│   └── saved/
│       ├── index.html          — collections index (2 collections)
│       ├── all-posts/
│       │   └── index.html      — 10 post tiles (shortcodes aaa111–jjj000)
│       ├── col-favs/
│       │   └── index.html      — 3 post tiles (aaa111, bbb222, ccc333)
│       └── col-trips/
│           └── index.html      — 3 post tiles (bbb222, ddd444, eee555; overlap is intentional)
├── p/
│   ├── aaa111/index.html       — single image post (sc1.jpg); outcome: enriched
│   ├── bbb222/index.html       — carousel of 3 images (sc2a–sc2c.jpg); outcome: enriched
│   ├── ccc333/index.html       — video post (sc3.mp4); outcome: enriched
│   ├── ddd444/index.html       — "Sorry, this page isn't available"; outcome: lost
│   └── eee555/index.html       — single image post with missing media (missing.jpg); outcome: enriched+media_failed
└── fake-media/
    ├── sc1.jpg                 — minimal 1×1 JPEG (333 bytes)
    ├── sc2a.jpg                — minimal 1×1 JPEG (333 bytes)
    ├── sc2b.jpg                — minimal 1×1 JPEG (333 bytes)
    ├── sc2c.jpg                — minimal 1×1 JPEG (333 bytes)
    └── sc3.mp4                 — minimal valid MP4 (ftyp + mdat, 40 bytes)
    (missing.jpg intentionally absent — exercises media_failed path)
```

Each grid post tile is a minimal `<a href="/p/SHORTCODE/">` anchor with a child `<img>`.

Each post-detail page uses `<article id="post" role="presentation">` as the post container,
consistent with the selector list in `post-detail.ts`.

The collections index lists 2 collections as `<a href="/you/saved/<slug>/">` anchors.

## Post-detail test matrix (E4)

| Shortcode | Post type       | Media files         | Expected content-script outcome | Expected media outcome |
|-----------|-----------------|---------------------|---------------------------------|------------------------|
| aaa111    | Single image    | sc1.jpg (exists)    | enriched                        | present                |
| bbb222    | Carousel (×3)   | sc2a/b/c.jpg (exist)| enriched                        | all present            |
| ccc333    | Video           | sc3.mp4 (exists)    | enriched                        | present                |
| ddd444    | Lost / 404      | —                   | lost (detects "Sorry..." text)  | —                      |
| eee555    | Missing media   | missing.jpg (absent)| enriched                        | media_failed           |

## Serving

```bash
cd tests/fixtures/fake-ig
python -m http.server 9090
```

Then open:
- Collections index:  http://localhost:9090/you/saved/
- All-posts grid:     http://localhost:9090/you/saved/all-posts/
- Favorites grid:     http://localhost:9090/you/saved/col-favs/
- Trips grid:         http://localhost:9090/you/saved/col-trips/
- Post detail (aaa111): http://localhost:9090/p/aaa111/
- Post detail (lost):   http://localhost:9090/p/ddd444/

## Dev-mode extension build

Build the extension with `EXT_DEV=1` to add `localhost:9090` to host_permissions
and content-script matches:

```bash
cd extension
EXT_DEV=1 pnpm build
```

Then load unpacked from `extension/dist` in Chrome (chrome://extensions → Load unpacked).

The extension injects:
- `saved-grid.ts` on `http://localhost:9090/*/saved/*` pages
- `post-detail.ts` on `http://localhost:9090/p/*` pages (dev manifest match needed — see note)

## Shortcodes used

| Shortcode | Appears in                        |
|-----------|-----------------------------------|
| aaa111    | all-posts, col-favs, p/aaa111     |
| bbb222    | all-posts, col-favs, col-trips, p/bbb222 |
| ccc333    | all-posts, col-favs, p/ccc333     |
| ddd444    | all-posts, col-trips, p/ddd444    |
| eee555    | all-posts, col-trips, p/eee555    |
| fff666    | all-posts only                    |
| ggg777    | all-posts only                    |
| hhh888    | all-posts only                    |
| iii999    | all-posts only                    |
| jjj000    | all-posts only                    |
