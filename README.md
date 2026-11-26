# Knowledge Management Demo

Save web articles, PDFs and YouTube videos, then ask questions about them. Answers are
grounded in your saved material and cite their sources (PDF page, video timestamp, link).

| Part | Stack | Hosted on |
| --- | --- | --- |
| `frontend/` | Next.js 14 (App Router, TypeScript, Tailwind) | Vercel |
| `backend/` | Python FastAPI | Render (free Web Service) |
| Auth, Postgres + pgvector, file storage | Supabase | Supabase |
| Embeddings, summaries, chat, video | Google Gemini API | - |

## Local development

Prerequisites: Node.js 18.17+ and Python 3.10+ (3.12 recommended).

```bash
# 1. Backend  (http://localhost:8000)
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then fill in the values
uvicorn app.main:app --reload --port 8000

# 2. Frontend  (http://localhost:3000) - in a second terminal
cd frontend
npm install
cp .env.example .env.local           # then fill in the values
npm run dev
```

Open <http://localhost:3000> (use `localhost`, not `127.0.0.1`). Health check:
<http://localhost:8000/health> returns `{"status":"ok"}`.

Tests: `cd backend && python -m pip install -r requirements-dev.txt && python -m pytest tests`
and `cd frontend && npm run test:wake` (cold-start retry logic; needs Node 22.18+).

> If the project folder lives in OneDrive, Next.js can fail with
> `EINVAL: readlink ... .next`. Delete `frontend/.next` and restart, or move the project
> outside OneDrive.

## Database setup (once per Supabase project)

In the Supabase dashboard -> **SQL Editor -> New query**, paste the **contents** of
`backend/migrations/all_in_one.sql` (not the file path) and run it. It creates the tables,
row-level security, indexes, hybrid search and the table grants.

Then create the private storage bucket for PDFs by running
`backend/migrations/002_storage.sql` the same way.

| File | Purpose |
| --- | --- |
| `001_init.sql` | tables, indexes, tsv trigger, RLS policies, `match_chunks` |
| `002_storage.sql` | private `sources` bucket (20 MB, PDFs only) |
| `003_hybrid_search.sql` | `hybrid_search` (vector + full-text, reciprocal rank fusion) |
| `004_grants.sql` | table privileges for `service_role` / `authenticated` |
| `all_in_one.sql` | 001 + 003 + 004 for a fresh database (run 002 separately) |

`backend/tests/rls_test.sql` is a two-fake-user test you can paste into the SQL editor to
confirm one user cannot see another's rows (it rolls back, so nothing persists).

## Environment variables

Every secret comes from the environment; nothing is hardcoded. Real values live only in
git-ignored `.env` / `.env.local` files and in the Vercel and Render dashboards.

### Backend - `backend/.env.example` (Render -> Environment)

| Variable | Required | Notes |
| --- | --- | --- |
| `SUPABASE_URL` | yes | Supabase -> Project Settings -> API -> Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | **Secret.** The service_role / secret key. Server-side only |
| `SUPABASE_JWT_SECRET` | yes | **Secret.** Project Settings -> JWT. Verifies legacy HS256 tokens (new projects use JWKS automatically) |
| `GEMINI_API_KEY` | yes | **Secret.** From Google AI Studio |
| `FRONTEND_ORIGIN` | yes | Your Vercel URL, e.g. `https://my-app.vercel.app`. Comma-separate several. This is the CORS allow-list |
| `ALLOW_LOCALHOST_ORIGINS` | prod: `false` | `true` (default) also allows `localhost`/`127.0.0.1` for local dev |
| `FRONTEND_ORIGIN_REGEX` | no | Extra allowed origins by regex, e.g. Vercel previews: `https://my-app-.*\.vercel\.app` |
| `GEMINI_CHAT_MODEL`, `GEMINI_VIDEO_MODEL`, `GEMINI_EMBEDDING_MODEL` | no | Model overrides |

### Frontend - `frontend/.env.example` (Vercel -> Settings -> Environment Variables)

| Variable | Required | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | yes | Same Project URL as above |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes | The **anon / publishable** key (safe for browsers). Never the service_role key |
| `NEXT_PUBLIC_API_URL` | yes | Your Render URL, e.g. `https://knowledge-base-api.onrender.com` (no trailing slash). Inlined at build time: **redeploy after changing it** |

## Sign-in setup (Supabase Auth)

Email/password works out of the box. Supabase requires new users to confirm their email;
to skip that while demoing, turn off **Authentication -> Sign In / Providers -> Email ->
Confirm email**.

**Google sign-in**

1. Google Cloud Console -> create a project -> **OAuth consent screen / Google Auth
   Platform** (External; add yourself under **Test users** while in Testing mode).
2. **Credentials -> Create credentials -> OAuth client ID -> Web application**. Under
   **Authorized redirect URIs** add `https://<project-ref>.supabase.co/auth/v1/callback`
   (the *Supabase* callback URL shown in the Google provider panel). Copy the Client ID
   and Secret.
3. Supabase -> **Authentication -> Sign In / Providers -> Google**: enable, paste the
   Client ID and Secret, save.
4. Supabase -> **Authentication -> URL Configuration**: Site URL and Redirect URLs
   (see the deploy checklist for production values).

---

## Deploy checklist

Do these in order. The two hosts need each other's URL, so there is one round trip:
Render first (to get its URL), then Vercel, then tell Render the Vercel URL.

### 0. Before you start
- [ ] Supabase is set up: migrations run, `sources` bucket created, Google provider
      configured (above).
- [ ] The repo is on GitHub with **no secrets committed**. `git status` must not list
      `backend/.env` or `frontend/.env.local` (they are git-ignored).
- [ ] You have the values: Supabase URL, anon key, service_role key, JWT secret, Gemini key.

### 1. Render (backend)
- [ ] Render dashboard -> **New -> Blueprint** -> connect the GitHub repo. It reads
      `render.yaml` (Python 3.12, `rootDir: backend`, free plan, health check `/health`).
- [ ] When prompted, fill in: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
      `SUPABASE_JWT_SECRET`, `GEMINI_API_KEY`, and `FRONTEND_ORIGIN` (put a placeholder
      such as `https://placeholder.vercel.app` for now). `ALLOW_LOCALHOST_ORIGINS` is
      preset to `false`.
- [ ] Wait for the first deploy to go **Live**. Copy the service URL
      (`https://....onrender.com`).
- [ ] Open `<render-url>/health` -> `{"status":"ok"}`.

### 2. Vercel (frontend)
- [ ] Vercel dashboard -> **Add New -> Project** -> import the same repo.
- [ ] **Root Directory: `frontend`** (Framework preset: Next.js is detected).
- [ ] Add environment variables (Production, and Preview if you want previews to work):
      `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
      `NEXT_PUBLIC_API_URL` (= the Render URL from step 1).
- [ ] **Deploy**. Copy the production URL (`https://....vercel.app`).

### 3. Connect them
- [ ] Render -> your service -> **Environment** -> set `FRONTEND_ORIGIN` to the exact Vercel
      URL (`https://....vercel.app`, no trailing slash). Saving redeploys automatically.
- [ ] Supabase -> **Authentication -> URL Configuration**: set **Site URL** to the Vercel
      URL and add `https://<vercel-url>/auth/callback` to **Redirect URLs** (keep the
      localhost entries if you still develop locally).
- [ ] Google Cloud Console needs **no change** - its redirect URI is Supabase's, not Vercel's.

### 4. Test the live site
- [ ] Open the Vercel URL -> redirected to `/login`.
- [ ] Sign up / sign in (and try **Continue with Google**).
- [ ] First load after idle shows the "Waking up the server..." banner, then the dashboard.
- [ ] Save an article; it goes `processing` -> `ready` with a title, summary and category.
- [ ] Upload a PDF, open its original; save a YouTube link.
- [ ] Ask a question in the chat; click a citation.
- [ ] Browser devtools -> Console/Network show no CORS errors.

### Troubleshooting a live deploy
| Symptom | Likely cause |
| --- | --- |
| Banner stays, then "server didn't respond" | `NEXT_PUBLIC_API_URL` wrong/unset (redeploy Vercel after fixing), or Render service failed - check its Logs |
| Network error / CORS error in console | `FRONTEND_ORIGIN` on Render doesn't exactly match the Vercel URL (scheme, no trailing slash) |
| Google login returns to `/login?error=...` | Vercel `/auth/callback` missing from Supabase Redirect URLs |
| `redirect_uri_mismatch` from Google | Redirect URI in Google Console isn't exactly the Supabase callback URL |
| "database isn't set up" / "missing table permissions" | Run the migrations in `backend/migrations` (see above) |
| YouTube video fails with "caption request blocked" | Cloud IPs are blocked by YouTube for the captions fallback; Gemini's native video path is the primary and usually works |

## Keeping the free Render instance awake (uptime pinger)

Render's free Web Service sleeps after ~15 minutes without traffic, and the next request
takes ~30-60 s to wake it. For a demo, ping `/health` every 5 minutes with a free monitor:

1. Create a free account at [uptimerobot.com](https://uptimerobot.com).
2. **Add New Monitor** -> Monitor Type **HTTP(s)**.
3. Friendly name `knowledge-base-api`; URL `https://<your-service>.onrender.com/health`.
4. Monitoring interval **5 minutes** (the free-plan minimum, under Render's 15-minute sleep).
5. (Optional) Set an alert contact so you're emailed if the API goes down. Save.

Notes: Render gives a workspace 750 free instance-hours per month, and one service running
24/7 uses about 730-744 - fine for a single service, but **pause the monitor after your
demo** so it doesn't burn hours. Even with the pinger, the app still handles a cold start
gracefully (the frontend shows "Waking up the server..." and retries for up to 60 s, and
the login page pre-warms the backend while you type your password).

## Project layout

```
backend/
  app/            main.py, auth.py, config.py, db.py, models.py
    routers/      health, me, sources, ask, conversations
    services/     ingest, chunking, gemini, youtube, qa, storage
  migrations/     SQL (see above)
  tests/          pytest suite + rls_test.sql
frontend/
  app/            pages: dashboard (/), /login, /reset-password, /auth/callback
  components/     dashboard, chat panel, source cards/modal, citation links
  lib/            api client, wake-up/retry helper, supabase clients
render.yaml       Render Blueprint for the backend
```
