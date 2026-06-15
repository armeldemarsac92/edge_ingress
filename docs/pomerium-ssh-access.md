# Pomerium SSH Certificate Access

This repository deploys Pomerium Core on the edge VPS as the SSH certificate
access plane. Tenant VMs do not run a Pomerium agent and do not join a control
plane. They only trust the Pomerium SSH User CA through native OpenSSH
certificate support.

## Scope

Managed here:

- Pomerium Core on the edge VPS.
- Authentik OIDC integration for Pomerium.
- Public Pomerium authenticate service through the existing HAProxy edge.
- Public Pomerium native SSH proxy port through the VPS firewall.
- Pomerium SSH User CA and SSH host keys generated on the VPS.
- A cloud-init snippet containing the public SSH User CA for OpenStack VMs.

Not managed here:

- Creating the Authentik OAuth2/OpenID Connect provider in the UI.
- Creating OpenStack VMs.
- Discovering OpenStack VM targets.
- Generating tenant-specific `pomerium_ssh_routes`.
- Installing authorized keys on tenant VMs.

## Access Model

```text
SSH client
    |
    | ssh <linux-user>@<route>@ssh.example.net -p 2222
    v
Pomerium native SSH proxy on the edge VPS
    |
    | Authentik OIDC browser login when required
    | short-lived OpenSSH user certificate signed by Pomerium
    v
OpenStack VM sshd
    |
    | TrustedUserCAKeys /etc/ssh/trusted_user_ca_keys/pomerium_user_ca.pub
    v
Linux account on the VM
```

Pomerium terminates the client SSH connection, enforces route policy, signs the
user certificate, and connects onward to the selected upstream SSH server.

## Authentik OIDC

Create an Authentik OAuth2/OpenID Connect provider for Pomerium:

```text
Application: Pomerium
Provider type: OAuth2/OpenID Connect
Client type: Confidential
Redirect URI: https://ssh.example.net/oauth2/callback
Issuer URL: https://auth.example.net/application/o/pomerium/
Scopes: openid, email, profile, groups, offline_access
```

Store the generated client ID and client secret outside plaintext inventory.
The role reads them from:

```text
POMERIUM_OIDC_CLIENT_ID
POMERIUM_OIDC_CLIENT_SECRET
```

The default provider URL is:

```text
https://auth.example.net/application/o/pomerium/
```

Override it with `POMERIUM_OIDC_PROVIDER_URL` if the Authentik provider slug is
different.

## HAProxy And TLS

HAProxy stays the only public HTTP/TLS entry point. The Pomerium role appends a
local HTTPS application route for:

```text
ssh.example.net -> 127.0.0.1:8443
```

Pomerium runs HTTP-only on loopback with `insecure_server: true`; HAProxy
terminates public TLS using:

```text
/etc/haproxy/certs/ssh.example.net.pem
```

Set `POMERIUM_HAPROXY_CERT_PATH` if the certificate lives elsewhere. If the PEM
is managed locally and copied by Ansible, set one of:

```text
POMERIUM_HAPROXY_PEM_SRC
POMERIUM_HAPROXY_CERT_SRC + POMERIUM_HAPROXY_KEY_SRC
```

The default public host list only includes `ssh.example.net`. If you
use a wildcard/SAN certificate and want extra hostnames routed to Pomerium, set:

```text
POMERIUM_HAPROXY_HOSTNAMES=ssh.example.net,pomerium.example.net
```

## Native SSH Port

Pomerium listens publicly on:

```text
0.0.0.0:2222
```

Override with:

```text
POMERIUM_SSH_PUBLIC_PORT=2222
POMERIUM_SSH_BIND_HOST=0.0.0.0
```

The role appends that TCP port to `edge_firewall_public_tcp_ports_extra`, so the
existing firewall phase exposes it.

## OpenStack VM Trust

The role writes this generated file on the edge VPS:

```text
/opt/edge/pomerium/public/cloud-init-trust-pomerium-ssh-ca.yaml
```

It contains the public Pomerium SSH User CA and an `sshd_config.d` snippet:

```text
TrustedUserCAKeys /etc/ssh/trusted_user_ca_keys/pomerium_user_ca.pub
```

Use that file as part of OpenStack user-data/vendor-data for tenant VM builds.
The trust anchor is public, but it should still be delivered through your
OpenStack provisioning path rather than fetched blindly from the internet at
first boot.

## Dynamic Routes

No VM route is hardcoded in this repository. Feed `pomerium_ssh_routes` from
OpenStack discovery, inventory generation, or a future sync playbook.

Example structure:

```yaml
pomerium_ssh_routes:
  - name: tenant-a-node-1
    host: 10.50.0.11
    port: 22
    policy:
      - allow:
          and:
            - claim/openstack_groups: tenant-a-admin
            - ssh_username_matches_claim: allowed_ssh_logins
```

Notes:

- `name` is the route name users put in the SSH username.
- `host` is the private address or DNS name Pomerium can reach from the edge.
- `policy` is required per route; the role does not create an
  allow-any-authenticated-user default.
- Pomerium Core supports generic claim matching on SSH routes. The dedicated
  `groups` criterion is Enterprise-only for SSH routes, so prefer explicit
  Authentik claims such as `openstack_groups` for Core deployments.

Connect with:

```bash
ssh armeldemarsac@tenant-a-node-1@ssh.example.net -p 2222
```

## Main Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `POMERIUM_IMAGE_TAG` | `v0.32.8` | Pomerium container tag. |
| `POMERIUM_AUTHENTICATE_DOMAIN` | `ssh.example.net` | Public authenticate service URL host. |
| `POMERIUM_OIDC_PROVIDER_URL` | `https://auth.example.net/application/o/pomerium/` | Authentik OIDC issuer base URL. |
| `POMERIUM_OIDC_CLIENT_ID` | unset | Authentik OAuth client ID. |
| `POMERIUM_OIDC_CLIENT_SECRET` | unset | Authentik OAuth client secret. |
| `POMERIUM_SSH_PUBLIC_PORT` | `2222` | Public native SSH proxy port. |
| `POMERIUM_HAPROXY_CERT_PATH` | `/etc/haproxy/certs/ssh.example.net.pem` | Public TLS PEM for HAProxy. |
| `pomerium_ssh_routes` | `[]` | Dynamic OpenStack SSH target routes. |

## Operator Checks

On the VPS:

```bash
systemctl is-active pomerium-compose
docker compose -f /opt/edge/pomerium/compose.yml ps
ss -ltnp | grep -E ':(2222|8443)\b'
sed -n '1,120p' /opt/edge/pomerium/public/cloud-init-trust-pomerium-ssh-ca.yaml
```

From a client:

```bash
ssh armeldemarsac@<route>@ssh.example.net -p 2222
```
