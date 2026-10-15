# Explore v1 design reference

The owner-approved design for the Explore workspace. **Read `SPEC.md` first.** It is the source of truth for behaviour, colours, data readiness and the tests every UI PR must add.

- `SPEC.md`: what to build, the rules, the colour tokens, what may ship now, and the required tests.
- `screenshots/`: the seven reference states (desktop 1440 × 1040 and phone 390 × 844).
- `design/`: the design source (Design Component `.dc.html` files plus `canvas.json`), with `sources.json`, the citation fields for the cells the design shows (a copy from `docs/data`). Reference only: these files need a design runtime this repo doesn't ship and must not be copied into `web/` or `docs/`.

This folder is reference material, not published site content. Don't link it from the site. Change it only when the owner approves a new design version (then add `explore-v2/`, don't edit this one).
