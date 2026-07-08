# Deployment Guide

Sales Genie has two deployables: the **backend** (must be public HTTPS) and the
**Chrome extension** (packaged, points at the backend). Deploy the backend
first, then point the extension at it, then publish.

Prerequisites already cloud-hosted: **Supabase** (database) and your external
API keys (LLM / AssemblyAI / SendGrid). You only need to host the backend
service + a Redis instance.

---

## 1. Backend

### Option A — Render (one-click Blueprint) — recommended
1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo. It reads [`render.yaml`](render.yaml)
   and provisions: **web service**, **Celery worker**, and **Redis**.
3. In the dashboard, fill the secret env vars (`sync: false` in the blueprint):
   `DATABASE_URL`, `DATABASE_URL_SYNC`, `ENCRYPTION_KEY`, `LLM key`, etc.
   `JWT_SECRET_KEY` is auto-generated. `REDIS_URL` is auto-wired.
4. Deploy. The web service runs `scripts/entrypoint.sh web`, which **runs
   `alembic upgrade head`** then starts uvicorn with `WEB_CONCURRENCY` workers.
5. Copy the service's HTTPS URL (e.g. `https://sales-genie-api.onrender.com`) and
   set **`PUBLIC_BASE_URL`** to it → redeploy (so email/Slack links are correct).

### Option B — Any Docker host / VPS
```bash
cp .env.example .env          # fill in production values
# set APP_ENV=production, PUBLIC_BASE_URL=https://your-domain, FLOWER_BASIC_AUTH=user:pass
docker compose -f docker-compose.prod.yml up -d --build
```
Then put a reverse proxy (Caddy/nginx/Traefik) in front of port 8000 for TLS.
The prod compose runs migrations on start, uses multiple workers, sets
`restart: always`, keeps Redis internal, and binds Flower to localhost behind
basic auth.

### Production safety
`APP_ENV=production` turns on strict startup validation — the app **refuses to
boot** if `JWT_SECRET_KEY` / `ENCRYPTION_KEY` are unset, `DATABASE_URL` points at
localhost, `PUBLIC_BASE_URL` isn't HTTPS, or the selected LLM key is missing.

Generate the secrets:
```bash
openssl rand -hex 32                                                   # JWT_SECRET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
```

### Verify
```bash
curl https://your-backend/health          # {"status":"ok","env":"production"}
```

---

## 2. Chrome extension

1. **Point it at the backend.** Load the extension (`chrome://extensions` →
   Load unpacked → `extension/`), open **Settings**, set **Backend URL** to your
   HTTPS URL, and **Save backend**. The extension will prompt for host
   permission for that domain (granted via `optional_host_permissions`).
2. **Zip it:**
   ```bash
   cd extension && zip -r ../sales-genie-extension.zip . -x "*.DS_Store"
   ```
3. **Publish:** https://chrome.google.com/webstore/devconsole → pay the one-time
   $5 fee → **New Item** → upload the zip → complete the listing (icon,
   screenshots, **privacy policy URL — required for audio recording**), justify
   each permission → submit for review.
4. **After first upload** you get the extension's permanent ID. Add it to the
   backend's `CORS_ORIGINS` (`chrome-extension://<id>`) and redeploy so only your
   published extension is allowed.

---

## 3. Deploy order (chicken-and-egg)

The extension ID only exists after the first store upload, so:

1. Deploy backend → get HTTPS URL → set `PUBLIC_BASE_URL`
2. Configure extension → zip → upload to store → get extension ID
3. Set backend `CORS_ORIGINS=chrome-extension://<id>` → redeploy
4. Submit the extension for review

---

## 4. Production checklist

- [ ] `APP_ENV=production`
- [ ] Strong `JWT_SECRET_KEY` + `ENCRYPTION_KEY` set
- [ ] `DATABASE_URL` / `DATABASE_URL_SYNC` → Supabase session pooler (port 5432)
- [ ] `PUBLIC_BASE_URL` = your HTTPS backend URL
- [ ] Managed Redis (`REDIS_URL`)
- [ ] LLM key set for the chosen `LLM_PROVIDER`
- [ ] SendGrid sender verified + `SENDGRID_FROM_EMAIL` set
- [ ] `FLOWER_BASIC_AUTH` set (don't expose Flower unauthenticated)
- [ ] `CORS_ORIGINS` pinned to the published extension id
- [ ] All keys that appeared in dev **rotated**
- [ ] (Multi-instance only) move audio spool from local disk to Cloudflare R2
      (`backend/storage/r2.py` stub) — single instance is fine on local disk

---

## 5. Known limitations before scale

- **Audio spool** is on local disk (`/tmp/sg_recordings`). Fine for one backend
  instance; for horizontal scaling, wire the R2 storage stub so any worker can
  read a session's chunks.
- **ChromaDB** persists to a mounted volume — single-node. For multi-node RAG,
  move to a hosted vector DB (Chroma server / pgvector / Pinecone).
- **Supabase RLS** is not enabled — isolation is enforced at the app layer.
  Add Row-Level Security policies for defense-in-depth in a true multi-tenant SaaS.
