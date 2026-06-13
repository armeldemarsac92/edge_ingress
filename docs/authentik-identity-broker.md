# Edge Authentik Identity Broker

This document describes the implementation target for running Authentik on the
edge VPS as the central identity broker for the edge environment.

## Scope

In scope:

- Authentik on `auth.example.net`.
- GitHub as an external OAuth source.
- Authentik-managed local users, groups, policies, and service access.
- Pomerium OIDC integration for SSH certificate access.
- Future OpenStack Keystone/Horizon federation strategy.

Out of scope for this phase:

- Future service integrations beyond Pomerium and OpenStack.
- Running a second public reverse proxy such as Caddy, Traefik, or NGINX.
- Open registration.

## Target Architecture

```text
Internet
    |
    | 203.0.113.10:80,443
    v
existing HAProxy on edge1
    |
    | auth.example.net
    v
Authentik server container on 127.0.0.1:9000
    |
    +-- PostgreSQL container
    +-- Redis container
    +-- Authentik worker container
```

The existing VPS HAProxy remains the only public reverse proxy. It terminates TLS
for `auth.example.net`, injects the required forwarding headers, and proxies
HTTP to the Authentik container on localhost.

For all other accepted `example.net` names, the existing HAProxy TLS SNI
passthrough and dynamic private DNS routing model remains unchanged.

## Docker Deployment

The Authentik stack is managed by Ansible under:

```text
/opt/edge/authentik
```

Services:

- `postgresql`: PostgreSQL database for persistent Authentik state.
- `redis`: cache, broker, and runtime support.
- `server`: Authentik web/API service.
- `worker`: Authentik background task worker.

Only the Authentik server publishes a host port, and that port is bound to
loopback:

```text
127.0.0.1:9000 -> authentik-server:9000
```

PostgreSQL and Redis are reachable only on the Docker network.

## PostgreSQL And Redis

PostgreSQL is the critical persistence layer. Its data lives in:

```text
Docker named volume authentik_postgresql
```

Redis data lives in:

```text
Docker named volume authentik_redis
```

The PostgreSQL password and Authentik secret key are generated once by Ansible
when `/opt/edge/authentik/.env` does not already exist. The role does not
overwrite that file on later runs.

Most Authentik configuration made in the UI is stored in PostgreSQL, not in the
Compose file. Re-running the playbook must not reset that state. The role keeps
default blueprint application opt-in through `authentik_apply_default_blueprints`
so an ordinary run does not reapply bootstrap/default blueprints over an already
configured deployment.

## HAProxy Routing

The HAProxy role supports local HTTPS applications with this model:

```yaml
edge_local_http_apps:
  - name: authentik
    hostnames:
      - auth.example.net
    backend_host: 127.0.0.1
    backend_port: 9000
    tls_cert: /etc/haproxy/certs/auth.example.net.pem
    hsts: true
```

The `443/tcp` frontend still starts in TCP mode and inspects SNI. When SNI is
`auth.example.net`, HAProxy sends the connection to a local TLS-terminating
frontend. That local frontend:

- presents `/etc/haproxy/certs/auth.example.net.pem`;
- speaks HTTP to Authentik on `127.0.0.1:9000`;
- sets `X-Forwarded-Proto: https`;
- sets `X-Forwarded-Host`;
- preserves `Host`;
- adds `X-Forwarded-For`;
- supports HTTP/1.1 WebSocket upgrade traffic.

`80/tcp` for `auth.example.net` redirects to HTTPS.

## TLS Strategy

HAProxy needs a PEM bundle containing the full certificate chain and private key:

```text
/etc/haproxy/certs/auth.example.net.pem
```

Recommended production strategy:

- Use DNS-01 ACME outside this role, or another existing certificate automation
  path.
- Deploy the PEM bundle to the VPS with root-only permissions. Either install it
  manually at `/etc/haproxy/certs/auth.example.net.pem`, or provide a local
  combined PEM source through Ansible:

```yaml
authentik_haproxy_pem_src: /secure/local/path/auth.example.net.pem
```

If the certificate and private key are separate files, use:

```yaml
authentik_haproxy_cert_src: /secure/local/path/auth.example.net.fullchain.pem
authentik_haproxy_key_src: /secure/local/path/auth.example.net.key
```

If the leaf certificate and intermediate chain are separate files, set:

```yaml
authentik_haproxy_cert_src: /secure/local/path/auth.example.net.crt
authentik_haproxy_chain_src: /secure/local/path/issuer-chain.pem
authentik_haproxy_key_src: /secure/local/path/auth.example.net.key
```

- Enable the HAProxy local app route only after the certificate exists.

The production inventory currently treats
`/etc/haproxy/certs/auth.example.net.pem` as externally managed on the VPS.
When the source certificate is renewed elsewhere, concatenate the full chain and
private key into that path and reload HAProxy after `haproxy -c` succeeds.

HTTP-01 ACME is possible but awkward because HAProxy already owns `80/tcp` and
`443/tcp` for many domains. DNS-01 avoids interrupting the public edge.

## GitHub OAuth App

Create a GitHub OAuth App:

```text
Application name: Edge Authentik
Homepage URL: https://auth.example.net
Authorization callback URL: https://auth.example.net/source/oauth/callback/github
```

Store the client ID and client secret outside plaintext inventory. Prefer
Ansible Vault or 1Password-backed secret injection.

## Authentik GitHub Source

Create a GitHub OAuth source in Authentik:

```text
Name: GitHub
Slug: github
Consumer key: GitHub OAuth client ID
Consumer secret: GitHub OAuth client secret
Scopes: user:email
```

Optionally add `read:org` later if GitHub organization/team membership should be
used as an additional signal. Do not make GitHub teams the primary authorization
source unless that is a deliberate policy choice.

## Invite-Only Enrollment

Open registration must stay disabled.

Use Authentik invitations:

- Create an enrollment flow with an invitation stage.
- Set authentication to require unauthenticated users.
- Disable `Continue flow without invitation`.
- Use short invitation expiry, for example 48 hours.
- Use single-use invitations where possible.

Group assignment should be controlled by Authentik, not by arbitrary invitation
custom attributes. For tenant onboarding, use either:

- separate enrollment flows per tenant, each with a User Write Stage that assigns
  the tenant group; or
- admin approval after enrollment.

Example groups:

```text
edge-admins
pomerium-users
pomerium-admins
tenant-alice
tenant-bob
```

## Pomerium OIDC

Create an Authentik OAuth2/OpenID Connect provider for Pomerium:

```text
Application: Pomerium
Provider type: OAuth2/OpenID Connect
Client type: Confidential
Redirect URI: https://authenticate.example.net/oauth2/callback
Issuer URL: https://auth.example.net/application/o/pomerium/
Scopes: openid, email, profile, groups, offline_access
```

Bind an authorization policy that only allows:

```text
pomerium-users
pomerium-admins
edge-admins
```

Pomerium Core can match arbitrary OIDC claims in SSH route policy. For
multi-tenant OpenStack access, prefer explicit Authentik property mappings such
as `openstack_groups` and `allowed_ssh_logins` instead of depending on
Enterprise-only group-directory policy criteria.

Recommended access model:

```text
tenant-alice
    -> Alice OpenStack project
    -> Alice VMs through Pomerium-issued OpenSSH certificates
    -> Alice Kubernetes resources through future Pomerium or OpenStack policy

tenant-bob
    -> Bob OpenStack project
    -> Bob VMs through Pomerium-issued OpenSSH certificates
    -> Bob Kubernetes resources through future Pomerium or OpenStack policy
```

## OpenStack Federation Strategy

Do not duplicate Authentik users into Keystone long term. Keystone should trust
Authentik through federation.

Preferred path:

```text
Authentik OIDC provider
    -> Keystone federation identity provider
    -> Keystone mapping rules
    -> Keystone groups
    -> OpenStack project role assignments
```

Example mapping:

```text
Authentik group tenant-alice
    -> Keystone group tenant-alice
    -> Alice project member role
```

Horizon should use Keystone WebSSO once Keystone federation is ready.

## Hardening

- Keep Docker-published ports bound to `127.0.0.1`.
- Do not expose PostgreSQL or Redis outside the Docker network.
- Pin Authentik, PostgreSQL, and Redis image versions.
- Do not mount `/var/run/docker.sock` into the Authentik worker for the initial
  deployment.
- Set Authentik trusted proxy CIDRs for the Docker network.
- Require MFA for `edge-admins`.
- Keep one break-glass local admin with a strong password and recovery material.
- Disable open enrollment.
- Rotate GitHub OAuth and OIDC client secrets.
- Back up before every upgrade.

## Backup And Restore

Back up:

```text
/opt/edge/authentik/.env
/opt/edge/authentik/compose.yml
/opt/edge/authentik/media
/opt/edge/authentik/custom-templates
/opt/edge/authentik/blueprints
PostgreSQL dump from the postgresql container
```

The Ansible role installs a backup script at:

```text
/opt/edge/authentik/bin/backup.sh
```

PostgreSQL is the authoritative data store. Redis is not the primary restore
target.

## First Access

For a fresh deployment, create the first local admin through:

```text
https://auth.example.net/if/flow/initial-setup/
```

After the first admin is created, use:

```text
https://auth.example.net/
```

The root URL redirects to Authentik's default authentication flow. If it redirects
to `/flows/-/default/authentication/?next=/` and shows a 404 on a fresh
deployment, the default blueprints may need to be applied explicitly. Rerun:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase4-authentik.yml \
  -e authentik_apply_default_blueprints=true
```

## Operational Checks

Local stack checks:

```bash
cd /opt/edge/authentik
docker compose ps
docker compose logs -f server worker postgresql redis
docker compose exec postgresql pg_isready -U authentik
docker compose exec redis redis-cli ping
curl -fsS http://127.0.0.1:9000/-/health/live/
curl -fsS http://127.0.0.1:9000/-/health/ready/
```

HAProxy checks:

```bash
haproxy -c -f /etc/haproxy/haproxy.cfg
ss -ltnp | grep -E ':(80|443|9000)\b'
curl -Ik https://auth.example.net/
```

Monitor:

- Authentik server and worker logs.
- failed login spikes;
- invitation creation/use;
- GitHub OAuth callback errors;
- OIDC provider errors for Pomerium;
- PostgreSQL disk usage;
- backup job success;
- HAProxy reload failures;
- certificate expiry.

## Implementation Status

Implemented in this repository:

- `roles/vps_authentik` Docker Compose deployment role.
- `playbooks/phase4-authentik.yml`.
- HAProxy template support for local TLS-terminating HTTP apps.
- Production inventory variables for `auth.example.net`.

Still required before public cutover:

- Publish DNS `auth.example.net A 203.0.113.10`.
- Provide `/etc/haproxy/certs/auth.example.net.pem`.
- Set `authentik_haproxy_enabled: true`.
- Create the GitHub OAuth App.
- Configure the GitHub source, invitation flow, groups, policies, and Pomerium
  OIDC provider in Authentik.

## References

- Authentik Docker Compose installation:
  <https://docs.goauthentik.io/install-config/install/docker-compose/>
- Authentik reverse proxy requirements:
  <https://docs.goauthentik.io/docs/install-config/reverse-proxy>
- Authentik GitHub source:
  <https://docs.goauthentik.io/users-sources/sources/social-logins/github/>
- Authentik invitations:
  <https://docs.goauthentik.io/users-sources/user/invitations/>
- Authentik OAuth2/OIDC provider:
  <https://docs.goauthentik.io/docs/add-secure-apps/providers/oauth2>
