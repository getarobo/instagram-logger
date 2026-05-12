# fake-IG fixture site

Static HTML that mirrors enough of Instagram's saved-posts structure to test
the content script (`saved-grid.ts`) without hitting real Instagram.

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
│           └── index.html      — 3 post tiles (bbb222, ddd444, eee555; overlap with col-favs is intentional)
```

Each post tile is a minimal `<a href="/p/SHORTCODE/">` anchor with a child `<img>`.

The collections index lists 2 collections as `<a href="/you/saved/<slug>/">` anchors.

## Serving

```bash
cd tests/fixtures/fake-ig
python -m http.server 9090
```

Then open:
- Collections index: http://localhost:9090/you/saved/
- All-posts grid:    http://localhost:9090/you/saved/all-posts/
- Favorites grid:    http://localhost:9090/you/saved/col-favs/
- Trips grid:        http://localhost:9090/you/saved/col-trips/

## Dev-mode extension build

Build the extension with `EXT_DEV=1` to add `localhost:9090` to host_permissions
and content-script matches:

```bash
cd extension
EXT_DEV=1 pnpm build
```

Then load unpacked from `extension/dist` in Chrome (chrome://extensions → Load unpacked).

The extension will inject `saved-grid.ts` on `http://localhost:9090/*/saved/*` pages,
allowing end-to-end smoke testing without a real Instagram session.

## Shortcodes used

| Shortcode | Appears in               |
|-----------|--------------------------|
| aaa111    | all-posts, col-favs      |
| bbb222    | all-posts, col-favs, col-trips |
| ccc333    | all-posts, col-favs      |
| ddd444    | all-posts, col-trips     |
| eee555    | all-posts, col-trips     |
| fff666    | all-posts only           |
| ggg777    | all-posts only           |
| hhh888    | all-posts only           |
| iii999    | all-posts only           |
| jjj000    | all-posts only           |
