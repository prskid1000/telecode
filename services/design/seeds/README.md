# TeleDesign bundled seeds

Default design systems and style archetypes shipped with TeleDesign (see `docs/teledesign.md` §3, §4.1, §4.4).
Everything here is original TeleDesign content except what `NOTICE` lists (shadcn/ui neutral colours — MIT,
open-design guidance — Apache-2.0, Lucide icon paths — ISC/MIT; fonts are OFL and linked, not bundled).

```
seeds/
  README.md  NOTICE
  styles/styles.json              16 style archetypes — the direction picker when no system is attached
  systems/<slug>/                 neutral (default) · editorial · midnight · playful · technical
    system.json                   {name, slug, description, status:"published", is_default, license, credits[], …}
    DESIGN.md                     Overview/Voice, Colour, Typography, Spacing & layout, Radius, Elevation,
                                  Motion, Iconography, Imagery, Components, Known gaps
    USAGE.md                      how an agent applies the system (Design mode + Pencil mode)
    tokens.css                    the only file with raw colour values; default theme on :root,
                                  other theme on [data-theme="light|dark"] (".dark" alias for shadcn habits)
    tokens.json                   same values: color{light,dark}{token:{value(oklch),hex}}, typography,
                                  spacing, radius, shadow{light,dark}, motion, component
    components.css                token-only styles for the class API (td-btn, td-input, td-card, …)
    specimen.css                  chrome for *.card.html (two-theme panes, swatches)
    manifest.json                 components[{name,group,path,card,global,props}], cards[{path,group,viewport,name}],
                                  fonts[{family,weights,roles,license,source,css}], themes[], runtime pins
    adherence.json                lint rules: allowed colour tokens, spacing scale, radius, fonts, font sizes/weights,
                                  component prop enums, system-specific don'ts
    system.lib.pen                .pen library (schema "2.19"): themes {Mode:[default, other]}, 72 variables bound to the
                                  tokens (colours per Mode), 34 reusable components named Component/Variant[/Size]
    components/<group>/<Name>.jsx        React 18, no build, IIFE → window.<Name>
    components/<group>/<Name>.card.html  specimen; first line <!-- @tdCard group="…" name="…" viewport="WxH" -->
    guidelines/{colors,typography,spacing}.card.html
  _build/                         generator (python _build/gen_seeds.py && python _build/gen_styles.py)
```

Components in every system: Button, Input, Card, Badge, Tabs, Dialog, Table, Nav (groups: actions, forms,
layout, data-display, navigation, overlays). The JSX and `components.css` are shared in shape; each system
differs through its tokens plus a few component tokens (`--radius-control`, `--button-case`, `--badge-font`,
`--card-shadow`, …) and per-system defaults (Tabs variant, Table density/striping).

## Editing

**Do not hand-edit files under `systems/`** — they are generated. Change `_build/seed_specs.py` (palettes, fonts,
scales, prose) or `_build/gen_seeds.py` (component code, CSS, card templates, .pen builder, manifest/adherence
shape), then regenerate. `_build/gen_styles.py` holds the 16 archetypes. The generator converts oklch → sRGB hex
for `.pen` (hex-only format), including `/ N%` alpha as `#RRGGBBAA`.

## Installing (for the store)

`services/design/store.py` keeps systems at `data/design/systems/<32-hex id>.json` + `systems/<id>/`. A seeder
should, for each `seeds/systems/<slug>/` not yet installed (match on `slug`): mint an id, write the record from
`system.json` (plus `id`, `created_at`, `updated_at`, `sources: [{type:"bundled", slug}]`), and copy the folder
as `systems/<id>/`. Only `neutral` has `is_default: true`; respect a user-chosen default if one already exists.
Seeds are not yet wired into the store — that is a separate change.

## Viewing cards

Cards load their JSX with `<script type="text/babel" src>`, which Babel standalone fetches over XHR, so they
must be served over HTTP (the proxy, or `python -m http.server` in `systems/`) — `file://` will not work.
React 18.3.1 / ReactDOM 18.3.1 / @babel/standalone 7.29.0 come from unpkg with SRI hashes; fonts from Google
Fonts. All 55 cards were rendered in headless Edge with zero console errors.

## Licensing rules for future seeds

- No pen.dev libraries or style archetypes, no Claude Design systems, no real-brand names or imitations.
- Fonts: SIL OFL only (Google Fonts `ofl/` directory); record family, weights and license in `manifest.json`.
- Icons: Lucide (ISC). Third-party tokens or text: MIT/Apache-2.0 with an entry in `NOTICE`.
