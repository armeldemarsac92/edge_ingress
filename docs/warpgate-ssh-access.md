# Warpgate SSH Access Broker

This repository deploys Warpgate on the edge VPS as the SSH access broker for
inventory nodes. Warpgate listens on a dedicated public SSH port and forwards
sessions to registered targets.

## Model

```text
operator ssh client
    |
    | warpgate.example.net:2222
    v
Warpgate on edge1
    |
    | SSH with Warpgate client key
    v
openstack_edge_nodes inventory hosts
```

The Warpgate web UI is exposed through HAProxy at `warpgate.example.net`. HAProxy
terminates TLS and proxies to the local Warpgate HTTPS listener on
`127.0.0.1:8888`.

The Warpgate SSH listener is published directly on `2222/tcp` and added to the
VPS firewall allow-list by the `phase5-warpgate.yml` playbook.

## Deployment

Run after Authentik is deployed:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase5-warpgate.yml
```

The playbook:

- installs the Warpgate Docker Compose stack under `/opt/edge/warpgate`;
- generates the initial local admin password and admin API token on the VPS;
- enables Warpgate's local admin API token support;
- registers SSH targets from `openstack_edge_nodes`;
- installs Warpgate's SSH client public key into each target user's
  `authorized_keys`;
- re-runs the VPS firewall and HAProxy roles so the SSH listener and web UI are
  reachable.

The generated secrets stay on the VPS in `/opt/edge/warpgate/.env` with root-only
permissions. They are not stored in inventory.

## Authentik OIDC

Create an Authentik OAuth2/OpenID Connect provider for Warpgate, then run the
playbook with:

```bash
WARPGATE_OIDC_CLIENT_ID=<client-id> \
WARPGATE_OIDC_CLIENT_SECRET=<client-secret> \
SSH_AUTH_SOCK=$HOME/.1password/agent.sock \
ansible-playbook playbooks/phase5-warpgate.yml
```

Without those environment variables, Warpgate is still installed and target
sync still works, but SSO is not enabled in the generated Warpgate config.

## Inventory Overrides

The sanitized inventory defaults are placeholders:

| Variable | Default | Purpose |
| --- | --- | --- |
| `WARPGATE_DOMAIN` | `warpgate.example.net` | Public web UI hostname. |
| `WARPGATE_SSH_PUBLIC_PORT` | `2222` | Public SSH port for Warpgate. |
| `WARPGATE_HAPROXY_CERT_PATH` | `/etc/haproxy/certs/<domain>.pem` | HAProxy TLS PEM bundle. |
| `WARPGATE_BASE_DIR` | `/opt/edge/warpgate` | Compose, config, and data directory. |
| `warpgate_image_tag` | `v0.23.4` | Warpgate container tag. |

Per-node target overrides can be set in host vars:

```yaml
warpgate_target_ssh_host: 10.250.11.2
warpgate_target_ssh_port: 22
warpgate_target_ssh_user: ansible
```

If these are not set, target registration uses `ansible_host`, port `22`, and
the node's `ansible_user`.

## Operator Use

After deployment, browse to:

```text
https://warpgate.example.net/
```

SSH through Warpgate:

```bash
ssh -p 2222 <warpgate-user>@warpgate.example.net
```

Warpgate then prompts for the target and forwards the session according to the
registered targets and policies.
