# DNS-01 Certificate Renewal — migration guide

Today obsyd.dev uses Caddy's default HTTP-01 challenge: Let's Encrypt
hits `http://obsyd.dev/.well-known/acme-challenge/*` on the origin VPS
(72.61.190.129) and Caddy serves the response inline. This works
because the public A-record for `obsyd.dev` points directly at the VPS.

If obsyd.dev is ever moved behind a CDN (the way valuekick.de is now —
see `~/.claude/projects/.../memory/project_obsyd_deploy_state.md` for
that incident), HTTP-01 will fail in a renewal-loop because the
challenge request lands at the CDN, not the origin. At that point you
need **DNS-01**: Caddy proves control of the domain by creating a TXT
record under `_acme-challenge.obsyd.dev`.

This document is the migration plan. **Do not run any of this until you
actually move obsyd.dev behind a CDN.** The current setup works fine.

---

## When this becomes necessary

You will know you need DNS-01 when, on the VPS:

```bash
sudo docker logs valuekick-caddy-1 --tail 50 | grep -i 'obsyd.*challenge'
```

shows repeated `challenge failed` errors with `http-01` for `obsyd.dev`.
(The same symptom the valuekick.de cert hit on 2026-05-20, fixed there
with `tls internal` because Hostinger CDN does its own public TLS — see
the parent state memory.)

If you're not behind a CDN, **don't migrate**. HTTP-01 is simpler.

---

## Prerequisites

1. **Hostinger DNS API token** — Hostinger's API is exposed under
   `https://developers.hostinger.com`. Issue a personal access token
   with `dns.write` scope from the hPanel → API.
2. **A Caddy build that includes the Hostinger DNS plugin.** The
   official `caddy:2-alpine` image does NOT include it. You will need
   either:
   - Use `caddy:2-builder-alpine` to build a custom image with the
     `caddy-dns/hostinger` provider (xcaddy), OR
   - Switch to a more universal DNS provider that Hostinger lets you
     delegate to (Cloudflare DNS is the simplest path — point obsyd.dev
     nameservers at Cloudflare, use the `caddy-dns/cloudflare` provider
     which is well-maintained and trivial).

The Cloudflare-DNS-delegation route is faster to set up; the
Hostinger-API route keeps DNS at one provider. Pick based on whether
you mind operating two DNS systems.

---

## Migration steps (Cloudflare-DNS route, recommended)

### 1. Add obsyd.dev to Cloudflare

Free Cloudflare account, add the domain, copy the assigned name servers.

### 2. Switch nameservers at Hostinger

hPanel → Domains → obsyd.dev → DNS / Name servers → Custom → paste the
two Cloudflare nameservers. Propagation takes 1–24 h. While it
propagates, **do not change anything else**.

Verify propagation:

```bash
dig +short NS obsyd.dev
# expect two cloudflare.com names
```

### 3. Mirror the obsyd.dev A-record into Cloudflare

In Cloudflare DNS: add A record `obsyd.dev → 72.61.190.129`, proxy
status **DNS-only (gray cloud)** for now. If/when you flip to a true
CDN frontend, change to proxied (orange cloud).

### 4. Cloudflare API token for Caddy

Cloudflare dashboard → My Profile → API Tokens → Create Token →
"Edit zone DNS" template, scoped to `obsyd.dev` only. Copy the token.

### 5. Build a Caddy image with the Cloudflare provider

`/home/jo/valuekick/Dockerfile.caddy`:

```dockerfile
FROM caddy:2-builder-alpine AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare

FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

In `docker-compose.prod.yml`, replace the caddy service `image:` with
a `build:` pointing at this Dockerfile, and add the API token via
`environment:` from the (gitignored) `.env`:

```yaml
  caddy:
    build:
      context: .
      dockerfile: Dockerfile.caddy
    environment:
      CLOUDFLARE_API_TOKEN: ${CLOUDFLARE_API_TOKEN}
      DOMAIN: ${DOMAIN}
      # …existing OPS_BASIC_AUTH_* etc.
```

### 6. Switch the Caddyfile block for obsyd.dev

In `/home/jo/valuekick/Caddyfile`, change the obsyd block's TLS line:

```
obsyd.dev, www.obsyd.dev {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    # …rest of block unchanged
}
```

### 7. Recreate Caddy and verify

```bash
cd /home/jo/valuekick
docker compose -f docker-compose.prod.yml up -d --build --force-recreate caddy
sleep 90  # Caddy needs a minute to obtain the cert via DNS-01
docker logs --tail 50 valuekick-caddy-1 | grep -iE 'obtain|cert|dns'
curl -sI https://obsyd.dev/health | head -3
```

The logs should show `certificate obtained successfully` for obsyd.dev
without any `challenge failed` entries.

---

## Mirror the Caddyfile + compose changes back into the obsyd repo

After the migration works, mirror the edits into `jo20ow/valuekick`
(commit message: "tls dns-01 for obsyd.dev block"). Same pattern as
commit `baa7afd` — keep the OBSYD-OWNED markers in place so the
valuekick session knows not to touch them. The obsyd repo gets a
follow-up commit pointing future readers at this doc.

## Rolling back

The old `caddy:2-alpine` image (no DNS plugin) is still functional for
the valuekick.de block (which uses `tls internal`). To roll back:

1. Revert the Caddyfile block to its previous content.
2. Revert `docker-compose.prod.yml` to `image: caddy:2-alpine` (drop
   the `build:` section).
3. `docker compose up -d --force-recreate caddy`.
4. Point obsyd.dev A-record back at 72.61.190.129 (already is, in the
   Cloudflare-DNS-only setup) — no change needed.
5. Optional cleanup: switch nameservers back to Hostinger.
