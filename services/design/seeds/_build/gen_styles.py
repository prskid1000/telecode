"""16 original TeleDesign style archetypes → services/design/seeds/styles/styles.json."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "styles" / "styles.json"
OFL = "SIL Open Font License 1.1"


def F(display, body, mono="JetBrains Mono"):
    return {"display": display, "body": body, "mono": mono, "license": OFL, "source": "Google Fonts"}


def P(bg, surface, fg, muted, border, accent, accent_fg, support=None):
    p = {"background": bg, "surface": surface, "foreground": fg, "muted": muted, "border": border, "accent": accent, "accent-foreground": accent_fg}
    if support:
        p["support"] = support
    return p


STYLES = [
    ("quiet-ledger", "Quiet Ledger", "Greyscale, precise, product-first. Near-black actions on white, hairline borders, generous whitespace. The safe default when nothing else is specified.",
     P("oklch(0.99 0 0)", "oklch(1 0 0)", "oklch(0.17 0 0)", "oklch(0.55 0 0)", "oklch(0.92 0 0)", "oklch(0.2 0 0)", "oklch(0.985 0 0)"),
     F("Inter", "Inter"), {"base": "8px", "surface": "12px"}, "comfortable", "Product screenshots, plain photography; no illustration.", "150ms functional fades", ["SaaS dashboards", "admin tools", "settings pages", "B2B landing pages"], "neutral"),
    ("morning-broadsheet", "Morning Broadsheet", "Newsprint warmth: off-white paper, black ink, one deep red accent, serif headlines over a serif text face, rules instead of boxes.",
     P("oklch(0.975 0.01 85)", "oklch(0.99 0.006 85)", "oklch(0.2 0.02 50)", "oklch(0.5 0.03 60)", "oklch(0.86 0.02 75)", "oklch(0.45 0.15 27)", "oklch(0.98 0.01 85)"),
     F("Fraunces", "Source Serif 4", "IBM Plex Mono"), {"base": "2px", "surface": "3px"}, "airy", "Documentary photography with captions; duotone archival images.", "Slow fades, 240-420ms, no bounce", ["long-form articles", "annual reports", "newsletters", "essays"], "editorial"),
    ("night-shift-console", "Night Shift Console", "Blue-black surfaces stepping up in lightness, an indigo action colour, cyan for data. Built for dark rooms and long sessions.",
     P("oklch(0.16 0.02 270)", "oklch(0.2 0.025 270)", "oklch(0.96 0.01 270)", "oklch(0.7 0.03 270)", "oklch(1 0 0 / 9%)", "oklch(0.56 0.2 275)", "oklch(0.99 0.005 270)", ["oklch(0.78 0.13 200)"]),
     F("Manrope", "Manrope"), {"base": "8px", "surface": "12px"}, "compact", "UI screenshots on dark cards; a single soft glow behind the hero.", "180ms standard, springy toggles", ["developer marketing", "analytics apps", "AI products", "status pages"], "midnight"),
    ("citrus-pop", "Citrus Pop", "Cream page, coral actions, lemon and lavender blocks, pill buttons with stacked shadows. Loud, tactile and friendly.",
     P("oklch(0.985 0.012 95)", "oklch(1 0 0)", "oklch(0.25 0.04 290)", "oklch(0.5 0.04 290)", "oklch(0.9 0.025 290)", "oklch(0.57 0.2 28)", "oklch(0.99 0.005 95)", ["oklch(0.93 0.12 100)", "oklch(0.92 0.06 300)"]),
     F("Fredoka", "Nunito", "DM Mono"), {"base": "9999px", "surface": "28px"}, "roomy", "Flat colourful illustration, cut-out photography on tinted blocks.", "Spring overshoot on entrances and success", ["consumer apps", "onboarding", "kids and education", "campaign pages"], "playful"),
    ("switchboard", "Switchboard", "Dense developer tooling: cool greys, teal signal, mono for anything machine-made, 4px radii, 30px controls.",
     P("oklch(0.99 0.002 250)", "oklch(1 0 0)", "oklch(0.2 0.01 250)", "oklch(0.5 0.012 250)", "oklch(0.9 0.005 250)", "oklch(0.5 0.11 170)", "oklch(0.99 0.002 250)"),
     F("IBM Plex Sans", "IBM Plex Sans", "IBM Plex Mono"), {"base": "4px", "surface": "6px"}, "dense", "No imagery; 1px line diagrams with mono labels.", "Near-instant, 80-200ms, no overshoot", ["consoles", "internal tools", "API docs", "log and table views"], "technical"),
    ("gallery-white", "Gallery White", "Museum-wall minimalism: bright white, graphite type, oversized margins, a single thin sans at large sizes; the work is the only colour.",
     P("oklch(1 0 0)", "oklch(0.985 0 0)", "oklch(0.25 0 0)", "oklch(0.6 0 0)", "oklch(0.94 0 0)", "oklch(0.25 0 0)", "oklch(1 0 0)"),
     F("Instrument Sans", "Instrument Sans", "DM Mono"), {"base": "0px", "surface": "0px"}, "airy", "Large, uncropped artwork or product photography on white; captions in small caps.", "Slow cross-fades, 400-600ms", ["portfolios", "galleries", "architecture studios", "product launches"], None),
    ("terracotta-studio", "Terracotta Studio", "Earthy and handmade: clay, sand and olive, a rounded humanist serif for headlines, soft 12px corners, grain textures.",
     P("oklch(0.96 0.02 70)", "oklch(0.98 0.012 75)", "oklch(0.28 0.04 45)", "oklch(0.52 0.05 50)", "oklch(0.86 0.03 60)", "oklch(0.58 0.13 42)", "oklch(0.98 0.01 70)", ["oklch(0.6 0.07 125)"]),
     F("Fraunces", "Nunito", "DM Mono"), {"base": "10px", "surface": "16px"}, "comfortable", "Warm natural-light photography, craft details, subtle paper grain.", "Gentle 250ms eases", ["ceramics and craft brands", "restaurants", "wellness", "independent shops"], None),
    ("field-guide", "Field Guide", "Naturalist notebook: moss green and parchment, a sturdy serif with a mono for specimen labels, numbered figures and marginal notes.",
     P("oklch(0.965 0.02 100)", "oklch(0.985 0.012 100)", "oklch(0.26 0.03 140)", "oklch(0.5 0.04 130)", "oklch(0.86 0.03 110)", "oklch(0.45 0.09 150)", "oklch(0.98 0.01 100)"),
     F("Source Serif 4", "Source Serif 4", "IBM Plex Mono"), {"base": "4px", "surface": "6px"}, "comfortable", "Botanical line drawings, specimen photography on plain backgrounds, labelled figures.", "Minimal; 200ms fades", ["outdoor and environmental orgs", "science explainers", "field reports", "museums"], None),
    ("arcade-chrome", "Arcade Chrome", "After-hours arcade: near-black, electric magenta and lime, a geometric grotesk in heavy weights, sharp 2px corners and glowing focus states.",
     P("oklch(0.14 0.02 300)", "oklch(0.19 0.03 300)", "oklch(0.97 0.01 300)", "oklch(0.7 0.04 300)", "oklch(1 0 0 / 12%)", "oklch(0.65 0.27 340)", "oklch(0.99 0 0)", ["oklch(0.88 0.22 130)"]),
     F("Space Grotesk", "Space Grotesk", "DM Mono"), {"base": "2px", "surface": "4px"}, "compact", "High-contrast 3D renders, pixel accents, neon on black.", "Snappy 120ms with glow pulses", ["games", "events and festivals", "music releases", "hackathons"], None),
    ("harbor-fog", "Harbor Fog", "Coastal calm: misty blue-greys, navy text, a sea-glass accent, soft 10px radii and lots of breathing room.",
     P("oklch(0.97 0.008 230)", "oklch(0.99 0.004 230)", "oklch(0.27 0.04 250)", "oklch(0.55 0.03 240)", "oklch(0.9 0.012 230)", "oklch(0.55 0.08 200)", "oklch(0.99 0.004 230)"),
     F("Manrope", "Manrope"), {"base": "10px", "surface": "16px"}, "airy", "Soft-focus landscape photography, muted tones, lots of sky.", "Slow 300ms eases, gentle parallax at most", ["healthcare", "insurance and finance", "travel", "calm consumer apps"], None),
    ("ink-and-vermilion", "Ink & Vermilion", "Two-colour print: black ink on warm white with a vermilion stamp accent, strong grid, bold sans headlines against a text serif.",
     P("oklch(0.98 0.008 80)", "oklch(1 0 0)", "oklch(0.16 0.01 60)", "oklch(0.48 0.02 60)", "oklch(0.85 0.01 70)", "oklch(0.6 0.21 33)", "oklch(0.99 0 0)"),
     F("Instrument Sans", "Source Serif 4", "IBM Plex Mono"), {"base": "0px", "surface": "0px"}, "comfortable", "Black-and-white photography, halftone treatments, vermilion overprints.", "Hard cuts; 120ms", ["posters", "cultural institutions", "book and publishing sites", "manifestos"], None),
    ("sunday-market", "Sunday Market", "Farmers-market cheer: tomato, basil and butter yellow on kraft-paper beige, chunky rounded type, hand-drawn dividers.",
     P("oklch(0.95 0.03 85)", "oklch(0.98 0.015 90)", "oklch(0.3 0.05 50)", "oklch(0.52 0.05 60)", "oklch(0.85 0.04 80)", "oklch(0.6 0.19 32)", "oklch(0.99 0.01 90)", ["oklch(0.6 0.13 140)", "oklch(0.88 0.13 95)"]),
     F("Fredoka", "Nunito", "DM Mono"), {"base": "14px", "surface": "20px"}, "roomy", "Bright produce photography, stickers, hand-lettered labels.", "Bouncy 220ms", ["food and grocery", "local events", "recipe sites", "community orgs"], None),
    ("deep-orbit", "Deep Orbit", "Space-program seriousness: ink-navy, a signal orange, thin technical grotesk, coordinate-style mono labels and fine grid lines.",
     P("oklch(0.18 0.03 255)", "oklch(0.22 0.035 255)", "oklch(0.95 0.01 250)", "oklch(0.68 0.03 250)", "oklch(1 0 0 / 10%)", "oklch(0.72 0.17 50)", "oklch(0.18 0.03 255)"),
     F("Space Grotesk", "Inter", "IBM Plex Mono"), {"base": "4px", "surface": "8px"}, "comfortable", "Satellite and telescope imagery, orbital diagrams, fine grid overlays.", "Measured 250ms, linear progress", ["aerospace and deep tech", "research labs", "climate data", "hardware launches"], None),
    ("paper-cut", "Paper Cut", "Layered construction paper: flat pastel planes with offset hard shadows, rounded sans, playful but orderly.",
     P("oklch(0.97 0.02 20)", "oklch(0.99 0.01 20)", "oklch(0.3 0.04 280)", "oklch(0.55 0.04 280)", "oklch(0.88 0.03 20)", "oklch(0.55 0.15 265)", "oklch(0.99 0 0)", ["oklch(0.85 0.08 20)", "oklch(0.86 0.08 170)"]),
     F("Nunito", "Nunito", "DM Mono"), {"base": "12px", "surface": "18px"}, "comfortable", "Paper-craft illustrations and collage, flat shapes with hard offset shadows.", "Slide-and-settle 240ms", ["education", "kids' products", "nonprofits", "explainers"], None),
    ("civic-plain", "Civic Plain", "Public-service clarity: high-contrast black on white, one strong blue for links and actions, large type, visible focus, zero decoration.",
     P("oklch(1 0 0)", "oklch(0.97 0 0)", "oklch(0.15 0 0)", "oklch(0.45 0 0)", "oklch(0.8 0 0)", "oklch(0.45 0.18 260)", "oklch(1 0 0)"),
     F("IBM Plex Sans", "IBM Plex Sans", "IBM Plex Mono"), {"base": "4px", "surface": "4px"}, "comfortable", "Minimal; functional photography only when it helps a task.", "None beyond focus and state changes", ["government and civic services", "forms-heavy flows", "accessibility-first products", "utilities"], None),
    ("velvet-hour", "Velvet Hour", "Evening luxury: aubergine and bronze, a high-contrast display serif, restrained letter-spaced sans labels, soft vignette gradients.",
     P("oklch(0.18 0.03 330)", "oklch(0.22 0.035 330)", "oklch(0.94 0.02 70)", "oklch(0.7 0.03 50)", "oklch(0.94 0.02 70 / 12%)", "oklch(0.72 0.1 65)", "oklch(0.18 0.03 330)"),
     F("Fraunces", "Instrument Sans", "DM Mono"), {"base": "2px", "surface": "4px"}, "airy", "Low-key moody photography, rich shadows, close-up textures.", "Slow 400-600ms fades", ["hospitality and hotels", "fashion and beauty", "fine dining", "premium events"], None),
]

FONT_LICENSES = {f: OFL for f in ["Inter", "JetBrains Mono", "Fraunces", "Source Serif 4", "Instrument Sans", "IBM Plex Sans", "IBM Plex Mono",
                                  "Manrope", "Fredoka", "Nunito", "DM Mono", "Space Grotesk"]}

doc = {
    "schema": "teledesign-styles/v1",
    "description": "Original TeleDesign style archetypes, offered as the direction picker when a project has no design system attached. Picking one binds its palette and fonts into the project's :root; `system` names the bundled seed system that implements it fully, when one exists.",
    "license": "MIT (TeleDesign seed content). All fonts are SIL OFL 1.1 via Google Fonts.",
    "fonts": FONT_LICENSES,
    "styles": [
        {"id": i, "name": n, "description": d, "palette": p, "fonts": f, "radius": r, "density": den, "imagery": im, "motion": mo, "use_for": u, "system": sysid}
        for (i, n, d, p, f, r, den, im, mo, u, sysid) in STYLES
    ],
}
assert len(doc["styles"]) == 16
assert len({s["id"] for s in doc["styles"]}) == 16
for s in doc["styles"]:
    for k in ("display", "body", "mono"):
        assert s["fonts"][k] in FONT_LICENSES, s["fonts"][k]
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
print("wrote", OUT)
