# Supply Chain Atlas — Design Patterns

Design reference for the Supply Chain Atlas dashboard (SEC EDGAR entity-resolution + relationship-graph pipeline). Hand this to any tool or person building the front end so every view shares one identity. When a component isn't covered here, derive it from the principles below rather than inventing a new style.

## Identity in one line

A **quant terminal with a graph at its heart.** Dense, monospace-flavored, information-first chrome — but the relationship graph is the hero of the product, not a widget buried in a tab. The chrome stays quiet and utilitarian so the graph and the data carry the attention.

Two moods, one system:
- **Terminal chrome** — feeds, tables, alerts, metrics. Compact, hairline-ruled, monospace numerics, minimal decoration.
- **Graph canvas** — the entity/relationship network. Given the most screen space, the strongest color, and the clearest labeling. This is what the product *is*.

## Principles

1. **Density is a feature, not a bug.** This audience (finance/quant, and a grader assessing a real system) reads dense data comfortably. Prefer tight rows, hairline dividers, and small type over airy cards. But density ≠ clutter — every pixel of ink must carry information.
2. **Color encodes entity type, nothing else.** Never decorate with color. A node or badge is colored *because of what it is* (company / subsidiary / insider / owner / supplier), or *because of a state* (alert severity, model health). If color isn't carrying meaning, it's gray.
3. **The graph is the hero.** In any view where the graph appears, it gets the largest region, the boldest nodes, and an always-visible legend. Chrome around it is deliberately muted.
4. **Monospace for anything columnar or numeric.** Tickers, timestamps, counts, metrics, IDs, filing codes. Sans is only for prose labels and headings. This alignment is what makes dense data scannable.
5. **State is always visible.** Live/stale indicator, last-eval time, model health, node/edge counts — the system's pulse is on screen at all times, terminal-style.

## Color

Two entity ramps drive the graph; everything else is neutral gray or a semantic state color. Do not exceed this palette — adding hues breaks the "color = meaning" contract.

### Entity types (graph nodes, filing badges, edges)

| Entity | Role | Light fill / stroke | Dark fill / stroke | Text on fill |
|---|---|---|---|---|
| Company | primary subject | `#378ADD` / `#185FA5` | `#0C447C` / `#85B7EB` | `#042C53` |
| Subsidiary | owned entity | `#1D9E75` / `#0F6E56` | `#085041` / `#5DCAA5` | `#04342C` |
| Insider (person) | Form 4 filer | `#7F77DD` / `#534AB7` | `#3C3489` / `#AFA9EC` | `#26215C` |
| Owner (13D/G/F) | stake holder | `#D4537E` / `#993556` | `#72243E` / `#ED93B1` | `#4B1528` |
| Supplier / customer | supply-chain edge | `#D85A30` / `#993C1D` | `#712B13` / `#F0997B` | `#4A1B0C` |

Supply-chain edges are drawn **dashed** to distinguish disclosed/free-text relationships (lower confidence) from structured edges (solid). This is an honest visual signal that matches the data's reliability tiers.

### States (alerts, model health, freshness)

| State | Meaning | Token |
|---|---|---|
| Danger | high-severity alert (insider cluster, large redline) | red — `#A32D2D` text, `#FCEBEB` bg |
| Warning | medium alert (shared auditor, moderate anomaly) | amber — `#854F0B` text, `#FAEEDA` bg |
| Info | informational (new edge, new filing) | blue — `#185FA5` text, `#E6F1FB` bg |
| Healthy | model ok, feed live | green — `#3B6D11` text, `#EAF3DE` bg |

### Neutrals

Everything structural — backgrounds, borders, chrome text, dividers — uses gray. Prefer CSS variables (`--surface-2`, `--surface-1`, `--surface-0`, `--text-primary`, `--text-secondary`, `--text-muted`, `--border`, `--border-strong`) so light/dark mode works automatically. Only reach for a literal gray hex if a variable genuinely doesn't fit.

Dark mode is mandatory. Every color above lists a dark variant; never hardcode a color that only works in one mode.

## Typography

- **Monospace** (`--font-mono`) — all data: tickers, timestamps, filing codes (`8-K 2.01`, `Form 4`), counts, metrics, IDs, table cells with numbers. This is the terminal signature.
- **Sans** (`--font-sans`) — headings, section labels, prose, button text, entity names in the graph.
- Never use serif except for a genuine editorial/quote moment (rare here).

Scale and weight:
- Section eyebrow labels: 10–11px, `letter-spacing: 0.5–1px`, `--text-muted`, uppercase is acceptable *only* for these terminal-style eyebrows (the one place caps are allowed).
- Body / row text: 11.5–14px.
- Headings: h1 20–22px, h2 18px, h3 16px, all weight 500.
- Metrics: 16–24px, weight 500, monospace.
- Two weights only: 400 and 500. Never 600/700 — too heavy against the chrome.

Everything else is **sentence case** (labels, buttons, headings, tooltips). The uppercase eyebrow is the single exception.

## Layout

- **Shell:** a persistent top bar showing `Supply Chain Atlas`, live/stale status, and `N nodes · M edges` counts. This is always visible — it's the terminal pulse.
- **Grid over cards.** Prefer regions divided by hairline borders (`0.5px solid var(--border)`) over floating rounded cards. Rounded cards are reserved for the graph container and standalone objects (a single entity detail record).
- **Corners:** small radius (`4–6px`) on chrome and controls; `12px` only on the graph canvas container and detail cards. Terminal density reads better with tighter corners.
- **Regions to support:**
  - **Filing feed** — reverse-chronological, monospace rows: `time · ticker · filing-code · tag`. Color the filing code by relevance, not decoratively.
  - **Graph canvas** — the hero. Force-directed or pre-computed layout, focus entity centered and enlarged, legend always docked beside or below it.
  - **Alerts rail** — severity-colored left-border rows (`border-left: 2px solid <state>`; note single-sided borders get `border-radius: 0`).
  - **Model/pipeline strip** — a row of monospace metrics: F1, drift status, last eval time, throughput.

## Graph canvas — the hero, spec'd

- Focus entity: largest node, centered, `company` blue (or its actual type).
- Edges: solid for structured relationships (Form 4, 13D/G/F, Exhibit 21 subsidiary); **dashed** for disclosed/free-text supply-chain relationships. This visually encodes the confidence tier.
- Node label: sans, inside or beside the node, sentence case, truncate long names.
- Legend: always visible, maps each color dot to its entity type. Non-negotiable — a colored graph with no legend is unreadable.
- Interaction: click a node to refocus the graph on it (`sendPrompt` or client-side re-layout); hover for a detail tooltip (entity name, type, canonical ID, edge count).
- Empty state: "Search for a company or person to explore its network." An invitation, not "no data."
- Performance: for large neighborhoods, cap visible nodes (e.g. top-N by edge weight) and offer "expand" — don't render 500 nodes at once.

## Components

- **Filing row:** `HH:MM` (muted mono) · ticker (mono, weight 500) · filing code (mono, colored) · tag (muted mono, right-aligned).
- **Alert row:** severity left-border, title (sans 500), detail (muted, one line). Keep to two lines.
- **Metric cell:** tiny muted uppercase label, large mono value below. No border between cells except a hairline divider.
- **Badge / pill:** colored background from the entity or state ramp; text uses the *darkest* stop of that same ramp — never black or generic gray on a colored fill.
- **Detail record** (single entity): the one place a full rounded card (`12px`, white surface, 0.5px border) is right. Header with entity name + type badge, then a hairline-divided key/value table (mono values).

## Copy voice

Terminal-terse but plain. Follow these:
- Sentence case everywhere except uppercase eyebrow labels.
- Name things by what the user recognizes: "insider cluster," "new relationship," "risk language changed" — not "Form-4 anomaly vector" or system internals.
- Active voice, verb-first buttons: "Explore network," "Add to watchlist" — not "Submit."
- Errors say what happened and what to do, no apology, no first person: "Couldn't reach EDGAR. Retrying in 30s."
- Empty states invite action, never "Nothing here yet."
- No exclamation marks, no "successfully," no filler ("simply," "just," "seamless").

## Quality floor (don't skip)

- Responsive down to a narrow viewport; the graph reflows or scrolls gracefully.
- Visible keyboard focus on every interactive element.
- Respect `prefers-reduced-motion` — no auto-animating graph physics if the user opts out; fall back to a static layout.
- Every text element readable in both light and dark mode (mental test: if the background were near-black, is this still legible?).
- Round every displayed number to sensible precision — no float artifacts (`0.87`, not `0.8699999`).

## What to avoid

- Rainbow/sequential coloring (step 1 blue, step 2 amber…). Color = entity type or state, full stop.
- Airy, generic-SaaS spacing that fights the terminal density.
- Decorative gradients, shadows, glows.
- A graph with no legend, or nodes with no type distinction.
- Title Case, ALL CAPS prose, exclamation marks, corporate filler.
