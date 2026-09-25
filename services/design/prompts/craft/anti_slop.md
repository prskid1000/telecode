<!-- Portions adapted from nexu-io/open-design (Apache-2.0) craft/anti-ai-slop.md, itself adapted
     from referodesign/refero_skill (MIT). See NOTICE. -->

# Anti-slop

The patterns below are the tells of default machine output. Each is checkable. Before you finish,
audit your work against this list; the lint and the verifier check the machine-detectable ones.

## Never (fix before shipping)

1. **Default indigo/violet as the accent** — Tailwind's `#6366f1 #4f46e5 #4338ca #8b5cf6 #7c3aed
   #a855f7` and their neighbours. Use the system's or direction's accent.
2. **Two-stop "tech" gradients** (purple→blue, blue→cyan, pink→orange) on heroes and backgrounds,
   or a gradient on every surface.
3. **Emoji as icons** in headings, buttons, lists or feature tiles — unless the brand itself uses
   emoji.
4. **Rounded card with a coloured left border** — the stock "callout / KPI tile". Drop the radius or
   the stripe.
5. **Invented proof** — "10× faster", "99.9% uptime", "Trusted by 10,000 teams", fake logos, fake
   testimonials. Real and sourced, or a labelled placeholder.
6. **Filler copy** — lorem ipsum, "Feature one/two/three", "Your headline here", generic
   "Streamline your workflow" lines that could sell anything.
7. **Hand-drawn SVG illustration** — people, faces, scenery, mascots, device mockups drawn from
   paths. Use a placeholder and ask for real material.
8. **Overused display faces** — Inter, Roboto, Arial, Helvetica, system-ui or Fraunces as the
   *display* face (fine as body where the system uses them).
9. **Designer furniture in the product** — viewport switchers, "Variant A/B" labels, settings
   panels, generated-by badges presented as app UI (Tweaks, hidden by default, is the exception).

## Avoid (should fix)

- The unvaried template skeleton: hero → three icon features → pricing → FAQ → CTA.
- An icon beside every heading; icons that repeat the label's meaning.
- Everything in a card; cards inside cards. Use a container only when it has a job.
- Uniform grids of identical tiles where content has different weights.
- The accent on more than two elements per screen.
- Beige / cream / peach page washes by default — only when the brand or direction asks for them.
- Hover states that grey out or lighten text.
- More than ~12 raw colour values outside the token block — tokens weren't honoured.
- Stock-photo CDN hotlinks (`unsplash`, `picsum`, `placehold.co`) — fragile and obvious.
- Decorative blobs, waves and glows with no meaning; glassmorphism on everything.
- Perfect symmetry with no tension — alternate a tight section with an open one.

## Adding soul without breaking rules

Aim for roughly four parts proven pattern to one part distinctive choice, and put the distinctive part
in one place:
- one bold move — a type choice, an unexpected proportion, one confident colour decision;
- voice — a button that says what happens ("Start tracking") instead of "Get started";
- one interaction the user remembers — a press that settles, a number that counts to its value;
- one detail only someone who used the product would add — a real keyboard hint, a status phrased
  the way the team actually says it.

The test: show a screenshot to someone outside the project. If they can't tell which product it
belongs to, it's a template.
