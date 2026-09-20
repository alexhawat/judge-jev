# judge-jev judgment funnel (interactive)

Live preview (GitHub Pages, `pages/funnel-preview` branch):

**https://alexhawat.github.io/judge-jev/funnel/**

Interactive React Flow diagram of the core judgment pipeline: input → state filter → one batched TypeSafe `system_one` call (screen / profile / locate / score / route questions) → answers → declarative YAML routing → confidence floor → verdict.

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

Open the URL printed by Vite (defaults to `http://localhost:4173/judge-jev/funnel/`).

## Re-deploy

Push to the `pages/funnel-preview` branch (or run the **Deploy funnel preview to GitHub Pages** workflow manually via Actions → workflow_dispatch).

Source lives in `web/funnel/`. The workflow builds with Vite and publishes `web/funnel/dist` to GitHub Pages.

If the site 404s after the first deploy, enable **Settings → Pages → Build and deployment → Source: GitHub Actions** once.
