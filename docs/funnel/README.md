# judge-jev judgment funnel (interactive)

Live preview (GitHub Pages, `pages/funnel-preview` branch):

**https://alexhawat.github.io/judge-jev/funnel/v2/** (cache-bust path; `/funnel/` redirects here)

Interactive React Flow diagram of the core judgment pipeline: input → state filter → one batched TypeSafe `system_one` call (screen / profile / locate / score / route questions) → answers → declarative YAML routing → confidence floor → verdict.

**Interactions:** hover path highlight + dimming, rubric edge labels, fixture **Play** animation (pass / fail / escalate / injection / confidence floor), deep link `?node=<id>`, **Esc** to reset.

## Local development

```bash
cd web/funnel
npm install
npm run dev
```

For a production-like preview with the GitHub Pages base path:

```bash
npm run build && npm run preview
```

Open the URL printed by Vite (defaults to `http://localhost:4173/judge-jev/funnel/v2/`).

## Re-deploy

Push to the `pages/funnel-preview` branch (or run the **Deploy funnel preview to GitHub Pages** workflow manually via Actions → workflow_dispatch).

Source lives in `web/funnel/`. The workflow builds with Vite and publishes to `gh-pages:funnel/v2/` plus a redirect at `funnel/index.html`.

If the site 404s after the first deploy, enable **Settings → Pages → Build and deployment → Source: Deploy from branch → `gh-pages` / `(root)`** once.
