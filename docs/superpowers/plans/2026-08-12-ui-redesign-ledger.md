# Ledger UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-dress the web UI as a paper register called *Still Here*, and merge the two "save post" pages into one that routes by hostname — without changing a single API contract.

**Architecture:** A new token layer and three serif faces replace the current Inter-based CSS. Templates move from a two-column split to one centred column of cards. `download.html` absorbs `instagram_download.html`; `/instagram` becomes a redirect. The two session panels become one drawer with two sections. All 44 element ids survive, so event wiring is untouched.

**Tech Stack:** Jinja2 templates, vanilla JS (no framework, no build step), hand-written CSS, Google Fonts.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-12-ui-redesign-ledger-design.md`.
- **No API route, payload, or status code may change.** The Android client calls `/auth/status`, `/auth/tiktok-cookies`, `/instagram/auth/status`, `/instagram/auth/cookies`, `/recordings/check-live`, `/live/stream`, `POST /downloads`, `POST /instagram/downloads` and file URLs. It must not need a release.
- Product name in UI copy is **Still Here**. Routes, ids, class names, the systemd unit and the `/tiktok` mount point keep their current names.
- Typography: **Fraunces** (headings/subjects), **Newsreader** (prose), **Cutive Mono** (labels/data/buttons). No sans-serif anywhere.
- Every animation must be disabled under `prefers-reduced-motion: reduce`.
- Bump the `?v=` query on every changed CSS/JS asset in the same commit, or browsers serve the old file.
- Element ids are frozen, with one deliberate exception. Six ids belong to the
  Instagram page that Task 4 deletes and retire with it:
  `instagram-url`, `instagram-download-form`, `instagram-download-notice`,
  `instagram-download-result`, `download-instagram-button`,
  `clear-instagram-download-form`. The merged page uses the `post-*` ids for
  both platforms. Every other id keeps its name — renaming one breaks
  `getElementById` wiring silently.
- Test command: `.venv/bin/python -m unittest discover -s tests`. Visual checks use the Browser pane against a locally-run app.
- Commit after every task. Do not deploy; deployment is a separate, explicit step.

---

### Task 1: Design tokens, fonts, and the paper

**Files:**
- Modify: `app/static/css/app.css` (replace the `:root` block and base element styles)
- Modify: `app/templates/base.html` (font links, `?v=` bump)

**Interfaces:**
- Consumes: nothing.
- Produces: the CSS custom properties every later task uses — `--board --board-2 --card --card-edge --ink --ink-soft --dim --rule --rule-soft --ink-2 --filed --pending --failed --shadow-card`; and the utility classes `.wrap` (660px centred column) and `.rise` + `.d1…d4` (staggered entrance).

- [ ] **Step 1: Swap the font links**

In `app/templates/base.html`, replace the Inter stylesheet line:

```html
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap">
```

with:

```html
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,300..900,0..100&family=Newsreader:opsz,wght@6..72,300..500&family=Cutive+Mono&display=swap">
```

- [ ] **Step 2: Replace the token block**

In `app/static/css/app.css`, replace the entire `:root { … }` block (currently lines 1–58, ending before `body[data-app="instagram"]`) with:

```css
:root{
  /* paper */
  --board:#ded6c4; --board-2:#e6dfd0;
  --card:#fbf8f0; --card-edge:#efe8da;

  /* ink */
  --ink:#221d17; --ink-soft:#4a4238; --dim:#7d7365;
  --muted:#7d7365;              /* kept: older rules still reference it */
  --rule:#cec4ad; --rule-soft:#e0d8c6;
  --line:#cec4ad; --line-strong:#b9ad92;

  /* series ink — the house ink is oxblood */
  --ink-2:#a8342a;

  /* stamps */
  --filed:#3f6b4a; --pending:#8a6a1f; --failed:#8f2c22;
  --danger:#8f2c22; --success:#3f6b4a; --warn:#8a6a1f;

  /* faces */
  --font-display:"Fraunces",Georgia,serif;
  --font-ui:"Newsreader",Georgia,serif;
  --font-mono:"Cutive Mono",ui-monospace,monospace;

  --shadow-card:0 1px 0 rgba(255,255,255,.9) inset, 0 14px 28px rgba(34,29,23,.13), 0 2px 4px rgba(34,29,23,.07);
  --radius-sm:0; --radius-md:0; --radius-lg:0;
}

/* An entry from the Instagram series prints in the second ink. */
[data-series="ig"]{ --ink-2:#2f4a7f; }
```

Delete the old `body[data-app="instagram"] { … }` block entirely, including the `.brand-mark` hue-rotate rule — series ink is per-entry now.

- [ ] **Step 3: Replace the page canvas**

Replace the existing `body { … }` rule with:

```css
body{
  margin:0;
  background:
    linear-gradient(180deg, rgba(34,29,23,.06), transparent 22%),
    repeating-linear-gradient(180deg, transparent 0 31px, var(--rule-soft) 31px 32px),
    radial-gradient(120% 70% at 50% 0%, var(--board-2), var(--board));
  color:var(--ink);
  font:400 15px/1.6 var(--font-ui);
  min-height:100vh;
}
/* paper grain — fixed so it does not scroll with content */
body::before{
  content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
  opacity:.5; mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/><feColorMatrix type='saturate' values='0'/></filter><rect width='140' height='140' filter='url(%23n)' opacity='.32'/></svg>");
}
.wrap{max-width:660px;margin:0 auto;padding:0 20px;position:relative;z-index:1}

h1,h2,h3,h4{font-family:var(--font-display);font-weight:400;color:var(--ink);margin:0}

/* one orchestrated load */
.rise{opacity:0;transform:translateY(12px);animation:rise .6s cubic-bezier(.2,.7,.25,1) forwards}
.d1{animation-delay:.10s}.d2{animation-delay:.18s}.d3{animation-delay:.26s}.d4{animation-delay:.34s}
@keyframes rise{to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){
  .rise{animation:none;opacity:1;transform:none}
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}
}
```

- [ ] **Step 4: Bump the asset version**

In `app/templates/base.html`, change `app.css?v=11` to `app.css?v=12`.

- [ ] **Step 5: Verify the paper renders**

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. Expected: cream/board background with visible horizontal ruling and grain; text in serif faces, not Inter. Layout will be broken — later tasks fix it. Confirm in DevTools that `Fraunces`, `Newsreader` and `Cutive Mono` all load (Network → Font).

- [ ] **Step 6: Commit**

```bash
git add app/static/css/app.css app/templates/base.html
git commit -m "ui: lay the paper — Ledger tokens, three serif faces, grain"
```

---

### Task 2: Masthead, footer, and the nav that names its platforms

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/static/css/app.css` (append masthead/footer/nav rules)

**Interfaces:**
- Consumes: tokens from Task 1.
- Produces: the shared chrome every page inherits — `.masthead`, `.mark`, `.title`, `.estd`, `.session-state`, `.tabs`, `.tab`, `.tabs-note`, `footer`. Keeps the ids `storage-note`, `session-pill`, `session-dot`, `session-toggle`.

- [ ] **Step 1: Replace the header markup**

In `app/templates/base.html`, replace the whole `<header class="topbar">…</header>` block (including the `app-switch` links and their inline SVGs) with:

```html
    <header class="masthead rise">
      <div class="masthead-line">
        <a class="brand" href="{{ base_path or '' }}/">
          <span class="mark" aria-hidden="true"></span>
          <span class="title">Still Here</span>
        </a>
        <span class="estd">A register of things broadcast once</span>
        <button class="session-state" type="button" id="session-toggle" aria-expanded="false">
          <span class="session-dot" id="session-dot"></span><span id="session-pill">Sessions</span>
        </button>
      </div>
      <nav class="tabs" aria-label="Primary">
        <a class="tab {{ 'is-active' if page_name == 'record' else '' }}" href="{{ base_path or '' }}/">Record live</a>
        <a class="tab {{ 'is-active' if page_name == 'watch' else '' }}" href="{{ base_path or '' }}/watch">Auto-record</a>
        <a class="tab {{ 'is-active' if page_name in ('download', 'instagram') else '' }}" href="{{ base_path or '' }}/download">Save post</a>
        <span class="tabs-note">Live recording · TikTok only</span>
      </nav>
    </header>
```

Note the `is-active` rule for Save post covers both `download` and `instagram` page names, because `/instagram` redirects there.

- [ ] **Step 2: Rewrite the title and body attributes**

Replace the `<title>` line with:

```html
  <title>Still Here — {{ page_name|default('register')|replace('_',' ') }}</title>
```

and the `<body>` tag with:

```html
<body>
```

The old `data-app="{{ platform }}"` drove the whole-page Instagram theme, which no longer exists.

- [ ] **Step 3: Wrap the page in the column**

Replace `<div class="shell">` with `<div class="wrap">`, and replace the page-header block

```html
        <h1 class="page-title">{% block page_title %}{% endblock %}</h1>
        <p class="page-lead">{% block hero_description %}{% endblock %}</p>
```

leave those blocks exactly as they are — Task 4 moves them into the sheet. Only the outer container changes here.

- [ ] **Step 4: Replace the footer markup**

```html
    <footer class="footer">
      <span>Beta — may contain bugs</span>
      <span><a href="mailto:bobby@dioriza.com">bobby@dioriza.com</a></span>
      <span class="sp" id="storage-note">Storage: …</span>
    </footer>
```

`app-common.js` sets `className` on `#storage-note`, so keep the id and let it own its classes.

- [ ] **Step 5: Style the chrome**

Append to `app/static/css/app.css`:

```css
.masthead{border-bottom:1.5px solid var(--ink);margin:26px 0;padding-bottom:0}
.masthead-line{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:inherit}
.mark{width:26px;height:26px;border:1.5px solid var(--ink-2);border-radius:50%;position:relative;flex:none}
.mark::after{content:"";position:absolute;inset:6px;background:var(--ink-2);border-radius:50%}
.title{font-family:var(--font-display);font-variation-settings:"opsz" 144,"SOFT" 40;font-weight:600;
       font-size:25px;line-height:1;letter-spacing:-.015em}
.estd{font:400 10px/1 var(--font-mono);letter-spacing:.16em;color:var(--dim);text-transform:uppercase}
.session-state{margin-left:auto;background:none;border:0;cursor:pointer;padding:4px 0;
               font:400 10px/1 var(--font-mono);letter-spacing:.14em;color:var(--dim);text-transform:uppercase}
.session-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--filed);margin-right:7px}
.session-dot.is-off{background:var(--pending)}
.tabs{display:flex;align-items:flex-end;gap:0;margin-top:14px;flex-wrap:wrap}
.tab{font:400 10px/1 var(--font-mono);letter-spacing:.16em;text-transform:uppercase;color:var(--dim);
     padding:9px 13px;text-decoration:none;position:relative;top:1px;border:1px solid transparent;border-bottom:none}
.tab:hover{color:var(--ink)}
.tab.is-active{color:var(--ink);background:var(--card);border-color:var(--rule);box-shadow:0 -2px 0 var(--ink-2) inset}
.tabs-note{margin-left:auto;font:400 9px/1 var(--font-mono);letter-spacing:.12em;color:var(--dim);
           text-transform:uppercase;padding:9px 0}
.footer{max-width:660px;margin:40px auto 30px;padding:14px 20px 0;border-top:1px solid var(--rule);
        display:flex;gap:18px;flex-wrap:wrap;font:400 10px/1.6 var(--font-mono);letter-spacing:.1em;
        color:var(--dim);text-transform:uppercase}
.footer a{color:var(--dim)}
.footer .sp{margin-left:auto}
.footer-item.warn{color:var(--failed);font-weight:400}
:focus-visible{outline:2px solid var(--ink-2);outline-offset:2px}
@media (max-width:620px){ .tabs-note{margin-left:0;width:100%;padding-top:0} }
```

- [ ] **Step 6: Verify**

Reload `http://127.0.0.1:8000/`. Expected: "Still Here" in Fraunces with the ring mark, three tabs with "Live recording · TikTok only" at the right, and the footer showing a real storage figure. Check 375px too — the note wraps to its own line rather than overflowing.

- [ ] **Step 7: Commit**

```bash
git add app/templates/base.html app/static/css/app.css
git commit -m "ui: masthead, nav that names its platforms, and footer"
```

---

### Task 3: The sheet and the filed card

**Files:**
- Modify: `app/static/css/app.css` (append)
- Modify: `app/static/js/record-page.js`
- Modify: `app/static/js/watch-page.js`
- Modify: `app/templates/record.html`, `app/templates/watch.html`

**Interfaces:**
- Consumes: tokens (Task 1), chrome (Task 2).
- Produces: `.sheet`, `.eyebrow`, `.field`, `.btn` / `.btn-quiet` / `.btn-sm`, `.filed-head`, `.job-card` (restyled, name kept), `.stamp` (replaces `.status-pill`), `.meta`, `.retention`, `.empty`.

- [ ] **Step 1: Style the sheet, fields and buttons**

Append to `app/static/css/app.css`:

```css
.sheet{background:var(--card);border:1px solid var(--card-edge);box-shadow:var(--shadow-card);
       padding:22px 24px 24px;position:relative}
.sheet::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ink-2)}
.eyebrow{font:400 10px/1 var(--font-mono);letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
.sheet h1{font-variation-settings:"opsz" 144,"SOFT" 30;font-weight:300;font-size:40px;line-height:1.02;
          letter-spacing:-.022em;margin:10px 0 6px}
.lede{font:400 15px/1.55 var(--font-ui);color:var(--ink-soft);max-width:46ch;margin:0}
.field{margin-top:20px}
.field label{display:block;font:400 10px/1 var(--font-mono);letter-spacing:.14em;text-transform:uppercase;
             color:var(--dim);margin-bottom:7px}
.field input{width:100%;background:transparent;border:0;border-bottom:1.5px solid var(--rule);
             font:400 15px/1.5 var(--font-mono);color:var(--ink);padding:8px 2px;transition:border-color .18s}
.field input::placeholder{color:#b3a892}
.field input:focus{outline:none;border-bottom-color:var(--ink-2)}
.row{display:flex;align-items:center;gap:14px;margin-top:22px;flex-wrap:wrap}
.btn{font:400 11px/1 var(--font-mono);letter-spacing:.18em;text-transform:uppercase;padding:13px 20px;
     border:1.5px solid var(--ink);background:var(--ink);color:var(--card);cursor:pointer;
     text-decoration:none;display:inline-block;transition:transform .12s,box-shadow .18s}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 14px rgba(34,29,23,.22)}
.btn-quiet,.btn-secondary,.btn-ghost{background:transparent;color:var(--ink);border-color:var(--rule)}
.btn-danger{background:transparent;color:var(--failed);border-color:var(--failed)}
.btn-warn{background:transparent;color:var(--pending);border-color:var(--pending)}
.btn-sm{font-size:10px;padding:9px 14px}
.notice{font:400 13px/1.5 var(--font-ui);color:var(--dim);border-left:2px solid var(--rule);
        padding-left:12px;margin-top:18px}
.notice.error{color:var(--failed);border-left-color:var(--failed)}
```

- [ ] **Step 2: Style the filed card**

Append:

```css
.filed-head{display:flex;align-items:baseline;gap:12px;margin:34px 0 14px}
.filed-head h2{font:400 13px/1 var(--font-mono);letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
.filed-head .rule{flex:1;height:1px;background:var(--rule)}
.job-card{background:var(--card);border:1px solid var(--card-edge);box-shadow:var(--shadow-card);
          padding:18px 20px;margin-bottom:14px;position:relative;overflow:hidden}
.job-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--rule)}
.job-card.live::before{background:var(--ink-2);animation:inkpulse 2.4s ease-in-out infinite}
@keyframes inkpulse{0%,100%{opacity:1}50%{opacity:.35}}
.job-header{display:flex;align-items:flex-start;gap:14px}
.job-id{font:400 10px/1.4 var(--font-mono);letter-spacing:.14em;color:var(--dim);text-transform:uppercase}
.job-title{font-family:var(--font-display);font-variation-settings:"opsz" 100,"SOFT" 40;font-weight:400;
           font-size:23px;line-height:1.15;letter-spacing:-.01em;margin:2px 0 0}
.job-message{font:400 14px/1.5 var(--font-ui);color:var(--ink-soft);margin:10px 0 0}
.stamp{margin-left:auto;flex:none;border:1.5px solid var(--filed);color:var(--filed);
       font:400 9px/1 var(--font-mono);letter-spacing:.18em;text-transform:uppercase;padding:6px 8px;
       transform:rotate(-4deg);opacity:.9;animation:stampin .5s cubic-bezier(.2,1.4,.4,1) both}
.stamp.soft{border-color:var(--pending);color:var(--pending)}
.stamp.bad{border-color:var(--failed);color:var(--failed)}
.stamp.good{border-color:var(--filed);color:var(--filed)}
@keyframes stampin{from{transform:rotate(-14deg) scale(1.5);opacity:0}to{transform:rotate(-4deg) scale(1);opacity:.9}}
.job-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:2px 16px;margin-top:14px;
           border-top:1px solid var(--rule);padding-top:10px}
.job-stats .wide{grid-column:1/-1}
.job-stats dt{font:400 9px/1.6 var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
.job-stats dd{font:400 13px/1.4 var(--font-mono);margin:0;color:var(--ink);overflow-wrap:anywhere}
.job-actions{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.retention{margin-top:12px;font:400 11px/1.4 var(--font-mono);letter-spacing:.1em;color:var(--dim);
           border-top:1px dashed var(--rule);padding-top:9px}
.empty{border:1.5px dashed var(--rule);text-align:center;padding:34px 20px}
.empty-icon{display:none}
.empty-title{display:block;font-family:var(--font-display);font-size:19px;margin-bottom:6px}
.empty span{font:400 14px/1.5 var(--font-ui);color:var(--dim)}
.elapsed-counter{font:400 12px/1 var(--font-mono);color:var(--dim);margin-left:8px}
@media (max-width:620px){ .job-stats{grid-template-columns:repeat(2,1fr)} }
```

- [ ] **Step 3: Rename `status-pill` to `stamp` in the two scripts**

`status-pill` appears 8 times across the scripts and `btn-secondary` once. In `app/static/js/record-page.js` and `app/static/js/watch-page.js`, replace every occurrence of `class="status-pill` with `class="stamp`, and `class="btn btn-secondary"` with `class="btn btn-quiet"`.

Verify none remain:

```bash
grep -rn "status-pill\|btn-secondary" app/static/js/
```

Expected: no matches in `record-page.js` or `watch-page.js`. (The download scripts are handled in Task 4; session panels in Task 5.)

- [ ] **Step 4: Give cards a register number**

In `record-page.js`, inside `renderJobs`, replace the job-id line

```js
              <span class="job-id">${escapeHtml(job.id)}</span>
```

with a register number and date, keeping the id available for `data-` lookups:

```js
              <span class="job-id">No. ${escapeHtml(job.id.slice(0, 4).toUpperCase())} · ${escapeHtml(formatDate(job.created_at))}</span>
```

Apply the same change in `watch-page.js` if it renders a job id.

- [ ] **Step 5: Move the page heading into the sheet**

In `app/templates/record.html`, replace the `layout layout-split` section with a single sheet. The ids `record-source`, `record-duration`, `recording-form`, `record-notice`, `record-helper-actions`, `jobs-container`, `refresh-recordings`, `toggle-auto-refresh`, `clear-record-form` must all survive:

```html
{% block page_content %}
<section class="sheet rise d1">
  <div class="eyebrow">Entry — live capture · TikTok</div>
  <h1>Record a broadcast<br>before it’s gone</h1>
  <p class="lede">Give a TikTok username or live URL. Access is checked first, then the deck starts and the file is held here until you take it.</p>

  <form id="recording-form" class="form">
    <div class="field">
      <label for="record-source">Subject — username or live URL</label>
      <input id="record-source" type="text" placeholder="@example_creator">
    </div>
    <div class="field">
      <label for="record-duration">Duration in seconds — optional</label>
      <input id="record-duration" type="number" min="1" placeholder="leave blank to record until the live ends">
    </div>
    <div class="row">
      <button class="btn" type="submit">Begin capture</button>
      <button class="btn btn-quiet" type="button" id="clear-record-form">Clear</button>
    </div>
  </form>
  <div id="record-notice" class="notice">Enter a TikTok username or live URL, then begin.</div>
  <div id="record-helper-actions" class="row"></div>
</section>

<div class="filed-head rise d2">
  <h2>Filed</h2><span class="rule"></span>
  <button class="btn btn-quiet btn-sm" type="button" id="refresh-recordings">Refresh</button>
  <button class="btn btn-quiet btn-sm" type="button" id="toggle-auto-refresh" aria-pressed="true">Auto: on</button>
</div>
<div id="jobs-container" class="rise d3"></div>
{% endblock %}
```

Make the equivalent change in `app/templates/watch.html`, keeping its ids `watch-form`, `watch-source`, `watch-duration`, `watch-notice`, `watch-container`, `refresh-watch-list`, `clear-watch-form`, and an eyebrow reading `Entry — standing order · TikTok`.

- [ ] **Step 6: Bump versions and verify**

In `record.html` change `record-page.js?v=4` to `?v=5`; in `watch.html` change `watch-page.js?v=3` to `?v=4`.

Seed a finished job and check the card renders:

```bash
.venv/bin/python - <<'PY'
from app.models.recording import RecordingJob, RecordingStatus, utc_now
from app.services.config import get_settings
from app.services.job_store import JobStore
from pathlib import Path
s = get_settings(); f = Path(s.output_dir) / "TK_demo.mp4"; f.write_bytes(b"x" * 2_400_000)
JobStore(s.jobs_file).save_job(RecordingJob(username="demo", status=RecordingStatus.finished,
    file_path=str(f), finished_at=utc_now(), fetched_at=utc_now()))
print("seeded")
PY
```

Open `http://127.0.0.1:8000/`. Expected: the sheet, then one filed card with a rotated "Ready" stamp, a four-column stats grid, and a retention line. Confirm the stamp animates in once and the page staggers.

- [ ] **Step 7: Commit**

```bash
git add app/static/css/app.css app/static/js/record-page.js app/static/js/watch-page.js app/templates/record.html app/templates/watch.html
git commit -m "ui: the sheet and the filed card"
```

---

### Task 4: Merge the two save-post pages

**Files:**
- Modify: `app/templates/download.html`
- Delete: `app/templates/instagram_download.html`
- Create: `app/static/js/save-page.js`
- Delete: `app/static/js/download-page.js`, `app/static/js/instagram-download-page.js`
- Modify: `app/main.py` (the `/instagram` route)
- Modify: `tests/test_app_reliability.py`

**Interfaces:**
- Consumes: sheet and card styles (Task 3).
- Produces: `initSavePage()` — one page that picks its endpoint from the URL's hostname; `platformFor(url)` returning `"tiktok" | "instagram" | null`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app_reliability.py` inside `AppReliabilityTests`, and **replace** the existing `test_instagram_page_renders_with_session_panel` with these two:

```python
    def test_instagram_url_redirects_to_the_shared_save_page(self) -> None:
        """/instagram was its own page; it is now one Save post page for both
        platforms. Old links must still land somewhere useful."""
        client = self.create_test_client()

        response = client.get("/instagram", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/download")

    def test_save_page_serves_both_platforms_and_both_sessions(self) -> None:
        client = self.create_test_client()

        response = client.get("/download")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Instagram session", response.text)
        self.assertIn("TikTok session", response.text)
        self.assertIn("Save post", response.text)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_app_reliability -v -k save_page`
Expected: FAIL — `/instagram` returns 200 rather than a redirect.

- [ ] **Step 3: Redirect the old route**

In `app/main.py`, replace the `instagram_page` handler:

```python
    @app.get("/instagram", response_class=HTMLResponse)
    def instagram_page(request: Request) -> HTMLResponse:
        return render_dashboard(request, "instagram_download.html", "instagram", platform="instagram")
```

with:

```python
    @app.get("/instagram")
    def instagram_page() -> RedirectResponse:
        """Kept so old links and bookmarks still work. Saving a post is one
        page now; it picks the platform from the URL you paste."""
        return RedirectResponse(url=f"{settings.root_path}/download")
```

and add the import at the top of the file:

```python
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
```

- [ ] **Step 4: Write the merged page**

Replace the whole body of `app/templates/download.html`:

```html
{% extends "base.html" %}

{% block page_content %}
<section class="sheet rise d1">
  <div class="eyebrow">Entry — saved post</div>
  <h1>Save a post<br>before it’s gone</h1>
  <p class="lede">Paste a TikTok or Instagram link. The register works out which is which and files the media here until you take it.</p>

  <form id="post-download-form" class="form">
    <div class="field">
      <label for="post-url">Link — TikTok or Instagram</label>
      <input id="post-url" type="text" placeholder="https://www.tiktok.com/@… or https://www.instagram.com/p/…">
    </div>
    <div class="row">
      <button class="btn" type="submit" id="download-post-button">Save post</button>
      <button class="btn btn-quiet" type="button" id="clear-post-download-form">Clear</button>
    </div>
  </form>
  <div id="post-download-notice" class="notice">Paste a link and the register will do the rest.</div>
</section>

<div class="filed-head rise d2"><h2>Filed</h2><span class="rule"></span></div>
<div id="post-download-result" class="rise d3"></div>
{% endblock %}

{% block page_scripts %}
<script src="{{ (base_path or '') ~ '/static/js/save-page.js?v=1' }}"></script>
<script>initSavePage();</script>
{% endblock %}
```

Delete `app/templates/instagram_download.html`.

- [ ] **Step 5: Write the merged script**

Create `app/static/js/save-page.js`. It replaces `download-page.js` and `instagram-download-page.js`, which were near-identical apart from their endpoint and the Instagram zip:

```js
/**
 * One page for both platforms. The endpoint is chosen from the link's
 * hostname — the same rule the backend validators and the Android UrlRouter
 * already use — so the person pasting a link never has to know which app
 * they are "in".
 */
function initSavePage() {
  const form = document.getElementById("post-download-form");
  const urlInput = document.getElementById("post-url");
  const notice = document.getElementById("post-download-notice");
  const resultContainer = document.getElementById("post-download-result");
  const submitButton = document.getElementById("download-post-button");
  const clearButton = document.getElementById("clear-post-download-form");
  let elapsedInterval = null;
  let elapsedSeconds = 0;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function platformFor(rawUrl) {
    let host;
    try { host = new URL(rawUrl.trim()).hostname.toLowerCase(); } catch { return null; }
    if (host === "tiktok.com" || host.endsWith(".tiktok.com")) return "tiktok";
    if (host === "instagram.com" || host.endsWith(".instagram.com") || host === "instagr.am") return "instagram";
    return null;
  }

  const endpointFor = (platform) => (platform === "instagram" ? "/instagram/downloads" : "/downloads");
  const fileName = (path) => String(path).split("/").pop();

  function emptyState() {
    return `<div class="empty"><span class="empty-title">Nothing filed yet</span>
      <span>Paste a TikTok or Instagram link above.</span></div>`;
  }

  function renderLoading(platform) {
    elapsedSeconds = 0;
    resultContainer.innerHTML = `
      <article class="job-card live" data-series="${platform === "instagram" ? "ig" : "tt"}">
        <div class="job-header">
          <div>
            <span class="job-id">Fetching · ${escapeHtml(platform)}</span>
            <h3 class="job-title">Working…</h3>
          </div>
          <span class="stamp soft">Pending</span>
        </div>
        <p class="job-message">Asking ${escapeHtml(platform)} for the media.
          <span class="elapsed-counter" id="download-elapsed">0s</span></p>
      </article>`;
    elapsedInterval = setInterval(() => {
      elapsedSeconds += 1;
      const el = document.getElementById("download-elapsed");
      if (el) el.textContent = `${elapsedSeconds}s`;
    }, 1000);
  }

  function clearLoading() {
    if (elapsedInterval) { clearInterval(elapsedInterval); elapsedInterval = null; }
  }

  function renderResult(download, platform) {
    const series = platform === "instagram" ? "ig" : "tt";
    const rows = (download.files || []).map((path, index) => `
      <div class="file-row">
        <div class="file-meta">
          <span class="file-name">${escapeHtml(fileName(path))}</span>
          <span class="file-path">${escapeHtml(path)}</span>
        </div>
        <a class="btn btn-sm" href="${appPath(download.file_urls[index])}">Take a copy</a>
      </div>`).join("");

    const zip = download.zip_url
      ? `<a class="btn btn-sm btn-quiet" href="${appPath(download.zip_url)}">Take all as zip</a>` : "";

    resultContainer.innerHTML = `
      <article class="job-card" data-series="${series}">
        <div class="job-header">
          <div>
            <span class="job-id">No. ${escapeHtml(download.download_id)} · ${escapeHtml(platform)}</span>
            <h3 class="job-title">${escapeHtml(download.files.length)} file${download.files.length === 1 ? "" : "s"} filed</h3>
          </div>
          <span class="stamp good">Filed</span>
        </div>
        <div class="file-list">${rows}</div>
        <div class="job-actions">${zip}
          <button class="btn btn-sm btn-danger" data-action="delete-download"
                  data-id="${escapeHtml(download.download_id)}" data-platform="${escapeHtml(platform)}">Discard</button>
        </div>
      </article>`;
  }

  async function submitDownload(event) {
    event.preventDefault();
    const url = urlInput.value.trim();
    const platform = platformFor(url);
    if (!platform) {
      setNotice(notice, "That does not look like a TikTok or Instagram link.", "error");
      return;
    }
    submitButton.disabled = true;
    setNotice(notice, `Fetching from ${platform}…`);
    renderLoading(platform);
    try {
      const response = await fetch(appPath(endpointFor(platform)), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "The download failed."));
      const download = await response.json();
      clearLoading();
      renderResult(download, platform);
      setNotice(notice, "Post filed.");
    } catch (error) {
      clearLoading();
      resultContainer.innerHTML = emptyState();
      setNotice(notice, error.message, "error");
    } finally {
      submitButton.disabled = false;
    }
  }

  resultContainer.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action='delete-download']");
    if (!button) return;
    const base = button.dataset.platform === "instagram" ? "/instagram/downloads" : "/downloads";
    try {
      const response = await fetch(appPath(`${base}/${button.dataset.id}`), { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Could not discard it."));
      resultContainer.innerHTML = emptyState();
      setNotice(notice, "Discarded from the server.");
    } catch (error) {
      setNotice(notice, error.message, "error");
    }
  });

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", () => {
    form.reset(); resultContainer.innerHTML = emptyState();
    setNotice(notice, "Cleared.");
  });
  resultContainer.innerHTML = emptyState();
}
```

Delete `app/static/js/download-page.js` and `app/static/js/instagram-download-page.js`.

The six `instagram-*` ids retire here along with the script that read them; the
merged page uses the `post-*` ids for both platforms. That is the only id change
in the whole redesign, and it is why the deletions and the new template must
land in the same commit — a half-applied version leaves a page whose script
cannot find its form.

- [ ] **Step 6: Style the file rows**

Append to `app/static/css/app.css`:

```css
.file-list{margin-top:14px;border-top:1px solid var(--rule)}
.file-row{display:flex;align-items:center;gap:14px;padding:11px 0;border-bottom:1px dashed var(--rule)}
.file-meta{min-width:0}
.file-name{display:block;font:400 14px/1.3 var(--font-mono);overflow-wrap:anywhere}
.file-path{display:block;font:400 10px/1.4 var(--font-mono);color:var(--dim);overflow-wrap:anywhere}
.file-row .btn{margin-left:auto;flex:none}
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS, including both new tests.

- [ ] **Step 8: Verify by hand, both platforms**

With the app running, open `http://127.0.0.1:8000/download` and paste a TikTok post URL, then an Instagram one. Expected: both file successfully; the Instagram card's margin rule and stamp print in process blue, TikTok's in oxblood; the Instagram result offers "Take all as zip" and the TikTok one does not. Then check `http://127.0.0.1:8000/instagram` redirects to `/download`.

- [ ] **Step 9: Commit**

```bash
git add app/templates/download.html app/static/js/save-page.js app/main.py tests/test_app_reliability.py app/static/css/app.css
git rm app/templates/instagram_download.html app/static/js/download-page.js app/static/js/instagram-download-page.js
git commit -m "ui: one Save post page that routes on the link you paste"
```

---

### Task 5: One session drawer, two sections

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/templates/_session_panel.html`, `app/templates/_ig_session_panel.html`
- Modify: `app/static/js/session-panel.js`, `app/static/js/ig-session-panel.js`
- Modify: `app/static/css/app.css`
- Modify: `app/main.py` (`render_dashboard` must pass both platforms' status)

**Interfaces:**
- Consumes: chrome (Task 2).
- Produces: a single `#session-drawer` containing both panels; `render_dashboard` supplies `cookies_configured`, `browser_login_status`, `ig_cookies_configured`, `ig_browser_login_status` on every page.

- [ ] **Step 1: Give every page both statuses**

In `app/main.py`, replace the platform branch inside `render_dashboard`:

```python
        if platform == "instagram":
            cookies_configured = instagram_cookie_service.is_configured()
            browser_login_status = instagram_browser_login_service.status()
        else:
            cookies_configured = cookie_service.is_configured()
            browser_login_status = browser_login_service.status()
```

with both, unconditionally — the drawer shows them side by side now:

```python
        cookies_configured = cookie_service.is_configured()
        browser_login_status = browser_login_service.status()
        ig_cookies_configured = instagram_cookie_service.is_configured()
        ig_browser_login_status = instagram_browser_login_service.status()
```

and add the two new names to the template context dict:

```python
                "ig_cookies_configured": ig_cookies_configured,
                "ig_browser_login_status": ig_browser_login_status,
```

- [ ] **Step 2: Include both panels on every page**

In `app/templates/base.html`, replace the platform-conditional include block with:

```html
  <aside class="session-drawer" id="session-drawer" aria-hidden="true">
    <div class="session-drawer-inner">
      <div class="session-drawer-head">
        <h2>Sessions</h2>
        <button class="btn btn-quiet btn-sm" type="button" id="session-close" aria-label="Close sessions">Close</button>
      </div>
      {% include "_session_panel.html" %}
      {% include "_ig_session_panel.html" %}
    </div>
  </aside>
```

and load both scripts unconditionally, replacing the `{% if platform == "instagram" %}` block:

```html
  <script src="{{ (base_path or '') ~ '/static/js/session-panel.js?v=3' }}"></script>
  <script src="{{ (base_path or '') ~ '/static/js/ig-session-panel.js?v=2' }}"></script>
  <script>initSessionPanel(); initIgSessionPanel();</script>
```

- [ ] **Step 3: Reduce each panel to a section**

In `_session_panel.html`, remove its own `<section class="session-drawer" id="session-drawer">` wrapper and its close button (the drawer owns both now), leaving a `<section class="session-section">` whose heading is `<h3>TikTok session</h3>`. Keep every id: `cookies-form`, `session-ss`, `clear-cookies`, `login-chrome`, `login-edge`, `capture-login`, `close-login`, `import-chrome`, `import-edge`, `session-notice`.

Do the same in `_ig_session_panel.html` with the heading `<h3>Instagram session</h3>`, keeping its ids, and change its status variables to the new names: `ig_cookies_configured` and `ig_browser_login_status`.

- [ ] **Step 4: Drop the duplicate drawer wiring**

Both `session-panel.js` and `ig-session-panel.js` toggle `#session-drawer` and bind `#session-close`. Two scripts binding the same elements would toggle twice and cancel out. In `ig-session-panel.js`, delete its drawer open/close wiring (the `session-toggle` and `session-close` listeners and any `aria-hidden` writes); leave only its form and button handlers. `session-panel.js` keeps ownership of the drawer.

Verify only one script references the drawer:

```bash
grep -n "session-drawer\|session-toggle\|session-close" app/static/js/session-panel.js app/static/js/ig-session-panel.js
```

Expected: matches in `session-panel.js` only.

- [ ] **Step 5: Style the drawer**

Append to `app/static/css/app.css`:

```css
.session-drawer{position:fixed;inset:0 0 0 auto;width:min(430px,100%);background:var(--card);
  border-left:3px solid var(--ink-2);box-shadow:-20px 0 50px rgba(34,29,23,.22);
  transform:translateX(100%);transition:transform .32s cubic-bezier(.2,.7,.25,1);
  overflow-y:auto;z-index:40;padding:24px}
.session-drawer[aria-hidden="false"]{transform:none}
.session-drawer-head{display:flex;align-items:baseline;gap:12px;border-bottom:1.5px solid var(--ink);
  padding-bottom:12px;margin-bottom:18px}
.session-drawer-head h2{font-family:var(--font-display);font-size:22px;font-variation-settings:"opsz" 120}
.session-drawer-head .btn{margin-left:auto}
.session-section{padding:18px 0;border-bottom:1px dashed var(--rule)}
.session-section h3{font-family:var(--font-display);font-size:18px;margin-bottom:4px}
.session-section:nth-of-type(2){--ink-2:#2f4a7f}
.step{display:flex;gap:12px;margin-top:14px}
.step-num{font:400 10px/1 var(--font-mono);color:var(--ink-2);border:1px solid var(--ink-2);
  padding:5px 7px;height:fit-content}
.step-body h3{font-size:15px;font-family:var(--font-ui)}
.step-body p{font:400 13px/1.5 var(--font-ui);color:var(--dim);margin:3px 0 8px}
@media (prefers-reduced-motion:reduce){ .session-drawer{transition:none} }
```

- [ ] **Step 6: Verify both sections work**

Reload any page and click the session state in the masthead. Expected: the drawer slides in with two sections, TikTok above Instagram, the Instagram section's accents in process blue. Confirm the drawer opens and closes on a single click (not twice), and that saving a session in either section still hits its own endpoint.

- [ ] **Step 7: Run the suite and commit**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: OK

```bash
git add app/templates/base.html app/templates/_session_panel.html app/templates/_ig_session_panel.html app/static/js/session-panel.js app/static/js/ig-session-panel.js app/static/css/app.css app/main.py
git commit -m "ui: one drawer holding both sessions"
```

---

### Task 6: Sweep the dead CSS and prove nothing broke

**Files:**
- Modify: `app/static/css/app.css`
- Modify: `app/templates/base.html` (final `?v=` bumps)

**Interfaces:**
- Consumes: everything above.
- Produces: no new interfaces.

- [ ] **Step 1: Delete rules for markup that no longer exists**

Remove from `app/static/css/app.css` every rule for classes the redesign dropped: `.topbar`, `.brand-text`, `.brand-mark`, `.app-switch`, `.app-switch-icon`, `.app-switch-icon-ig`, `.app-switch-label`, `.layout`, `.layout-split`, `.surface`, `.surface-soft`, `.card-head`, `.page-title`, `.page-lead`, `.hero`, `.shell`, `.status-pill`, `.footer-list`.

Check none are still referenced:

```bash
grep -rn "topbar\|app-switch\|layout-split\|card-head\|status-pill\|footer-list\|class=\"shell\"" app/templates app/static/js
```

Expected: no matches.

- [ ] **Step 2: Confirm every class the JS emits has a rule**

```bash
for c in btn btn-danger btn-quiet btn-warn elapsed-counter empty empty-title file-list file-meta file-name file-path file-row job-actions job-card job-header job-id job-message job-stats job-title live stamp wide notice; do
  grep -q "\.$c" app/static/css/app.css || echo "MISSING RULE: .$c"
done
echo "check complete"
```

Expected: `check complete` with no MISSING lines.

- [ ] **Step 3: Screenshot every page at both widths**

With the app running and one job plus one download seeded, capture `/`, `/watch`, `/download` at 1440px and at 375px. Expected: single column, no horizontal scrollbar at 375px, nav note wrapping rather than overflowing, cards legible.

- [ ] **Step 4: Check reduced motion**

In DevTools → Rendering → "Emulate CSS prefers-reduced-motion: reduce", reload. Expected: content appears immediately, no stagger, no stamp animation, no pulsing margin rule.

- [ ] **Step 5: Prove the Android client is unaffected**

The redesign must not have touched an API. Replay the client's exact calls against the running app:

```bash
B=http://127.0.0.1:8000
curl -s $B/auth/status | python3 -m json.tool | head -3
curl -s $B/instagram/auth/status | python3 -m json.tool | head -3
curl -s -X POST -H 'content-type: application/json' -d '{"username":"someone"}' $B/recordings/check-live | python3 -c "import json,sys; d=json.load(sys.stdin); print('check-live keys:', sorted(d))"
```

Expected: `/auth/status` and `/instagram/auth/status` return `{configured, cookie_file}`; check-live returns the keys `can_record, is_live, message, room_id, url, username`. Any missing key is a break — the Kotlin models expect them.

- [ ] **Step 6: Final version bumps and commit**

Ensure `app.css?v=12`, `app-common.js?v=3`, `record-page.js?v=5`, `watch-page.js?v=4`, `save-page.js?v=1`, `session-panel.js?v=3`, `ig-session-panel.js?v=2` in the templates.

```bash
git add app/static/css/app.css app/templates/base.html
git commit -m "ui: sweep the CSS the old layout left behind"
```

---

## Deployment

Not part of any task. When the plan is complete and the suite is green:

```bash
cd /opt/ttl-downloader && git pull && sudo systemctl restart ttl-downloader
```

Then load the site and hard-reload once (⇧⌘R) to defeat any cached CSS, and confirm `/instagram` redirects rather than 404s.
