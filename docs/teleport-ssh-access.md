# Teleport SSH Certificate Access

This repository deploys Teleport on the edge VPS as the SSH certificate access
plane. Tenant VMs do not run the Teleport agent and do not join the Teleport
cluster.

## Scope

Managed here:

- Teleport Auth Service and Proxy Service on the edge VPS.
- Public Teleport web access through the existing HAProxy edge.
- Public Teleport SSH proxy port through the VPS firewall.
- Teleport OIDC connector for Authentik.
- Teleport roles mapped from Authentik group claims.

Not managed here:

- Installing Teleport on OpenStack VMs.
- Enrolling OpenStack VMs with `teleport ssh_service`.
- Installing authorized keys on tenant VMs.
- Creating OpenSSH node resources or host certificates for tenant VMs.

## OpenSSH VM Trust Contract

OpenStack VM provisioning should configure `sshd` to trust the Teleport OpenSSH
CA:

```text
TrustedUserCAKeys /etc/ssh/teleport_openssh_ca.pub
```

The public CA can be queried from the Teleport proxy:

```text
https://teleport.example.net/webapi/auth/export?type=openssh
```

That provisioning flow is outside this repository. This role only installs the
edge Teleport services and manages the Authentik OIDC connector.

## Authentik OIDC

Create an Authentik OAuth2/OpenID Connect provider for Teleport:

```text
Application: Teleport
Provider type: OAuth2/OpenID Connect
Client type: Confidential
Redirect URI: https://teleport.example.net/v1/webapi/oidc/callback
Issuer URL: https://auth.example.net/application/o/teleport/
Scopes: openid, email, profile, groups
```

Then run phase 5 with the client credentials in the environment:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock \
TELEPORT_OIDC_CLIENT_ID=<client-id> \
TELEPORT_OIDC_CLIENT_SECRET=<client-secret> \
ansible-playbook playbooks/phase5-teleport.yml
```

Without those two environment variables, Teleport is installed but the Authentik
OIDC connector is skipped.

## Main Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEPORT_DOMAIN` | `teleport.example.net` | Public Teleport hostname. |
| `TELEPORT_CLUSTER_NAME` | `teleport_domain` | Teleport cluster name. |
| `TELEPORT_VERSION` | `18.8.3` | Teleport package version passed to the official install script. |
| `TELEPORT_EDITION` | `oss` | Teleport edition passed to the official install script. |
| `TELEPORT_SSH_PUBLIC_PORT` | `3023` | Public Teleport SSH proxy port. |
| `TELEPORT_OIDC_CLIENT_ID` | unset | Authentik OAuth client ID. |
| `TELEPORT_OIDC_CLIENT_SECRET` | unset | Authentik OAuth client secret. |
| `TELEPORT_ADMIN_GROUP` | `teleport-admins` | Authentik group mapped to the `edge-admin` Teleport role. |
| `TELEPORT_DEFAULT_LOGINS` | `edge_vps_ssh_user` | Comma-separated Unix logins allowed by the default role. |

## Operator Checks

On the VPS:

```bash
systemctl is-active teleport
tctl status
ss -ltnp | grep -E ':(3023|3080)\b'
```

From a client:

```bash
tsh login --proxy=teleport.example.net
```
