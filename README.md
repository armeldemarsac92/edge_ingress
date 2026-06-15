# VPS WireGuard Edge

Ansible-managed public edge for one public IPv4 address and one or more private
provider networks.

The VPS exposes one public IPv4 address and reaches one or more private provider
networks through WireGuard tunnels to edge nodes. The included sanitized example
uses HAProxy for public HTTP/TLS/TCP proxying, FRR eBGP with BFD for routing,
Unbound for private DNS resolution, and nftables for the node forwarding/SNAT
boundary.

## Inventory Model

The repository is intended to be inventory-driven. Hostnames, SSH users, public
key stubs, provider CIDRs, accepted domains, DNS zones, WireGuard addresses, BGP
ASNs, SNAT IPs, and proxy forwards should be supplied by inventory variables.

The included `inventories/production` directory is sanitized for publication.
Override its placeholders through local inventory files or environment-backed
variables before applying it to a real environment.

| Role | Host | Address |
| --- | --- | --- |
| VPS edge | `edge1` | `203.0.113.10` |
| OpenStack edge node | `edge-node1` | `edge-node1.example.net`, provider `10.200.0.11` |
| OpenStack edge node | `edge-node2` | `edge-node2.example.net`, provider `10.200.0.12` |
| OpenStack edge node | `edge-node3` | `edge-node3.example.net`, provider `10.200.0.13` |

Deployed defaults:

- Public edge proxy: `edge_proxy_backend: haproxy`
- Routing mode: `edge_routing_mode: frr`
- Provider network: `10.200.0.0/24`
- Accepted public domains: `example.net` and subdomains
- Private DNS forwarding zone: `apps.example.net`
- VPS firewall role: enabled in production with `edge_firewall_backend: ufw`
- VPS security role: CrowdSec, HAProxy SPOA bouncer, firewall bouncer, and Fail2ban
- Prometheus scrape endpoints: node exporter and CrowdSec metrics on the VPS
  WireGuard loopback address
- Generic TCP forwards: none by default with `edge_tcp_forwards: []`
- Minecraft routing: not enabled with `edge_minecraft_enabled: false`

WireGuard private keys are generated and stored only on the target hosts under
`/etc/wireguard/keys`. They are not stored in inventory and are not printed by
Ansible tasks.

## Architecture

```text
public clients
    |
    | edge_vps_public_endpoint:80,443
    | edge_tcp_forwards[*].public_port when configured
    v
[edge_vps host]
    - HAProxy listens on 80/tcp and 443/tcp
    - Unbound listens on 127.0.0.1:53 for private DNS zones
    - FRR receives provider_cidr over BGP from edge nodes
    - WireGuard links are generated from wireguard_links
    |
    +-- wireguard_links.<node>.vps_if <-> wireguard_links.<node>.node_if
        on each host in openstack_edge_nodes
        |
        v
    provider_cidr
```

### Routing

FRR runs on the VPS and on each node in `openstack_edge_nodes`.

- VPS local AS: `edge_frr_local_as`
- Node AS: `edge_frr_node_as_base + edge_node_id`
- BGP runs over the WireGuard point-to-point addresses.
- BFD is enabled for fast peer failure detection.
- `edge_frr_maximum_paths` defaults to the number of configured WireGuard links.
- The VPS installs an ECMP BGP route for `provider_cidr` through all available
  WireGuard paths.
- The stable VPS source/service address from `edge_vps_loopback_addr` is managed
  as a `systemd-networkd` address on the loopback interface.

Expected route on the VPS:

```text
provider_cidr proto bgp
    nexthop via wireguard_links.<node>.node_addr dev wireguard_links.<node>.vps_if
    ...
```

The previous static route service is disabled when `edge_routing_mode: frr` is
selected. The static route role remains available as a simpler fallback.

### Domains And DNS

The edge separates two related concepts:

- `edge_public_domains`: host suffixes HAProxy accepts in HTTP `Host` and TLS
  SNI. In the sanitized inventory this is `example.net`, which means
  `example.net` and any `*.example.net` name can enter the proxy.
- `edge_private_dns_zones`: DNS zones the VPS resolver forwards privately over
  WireGuard. In the sanitized inventory this is `apps.example.net` because the internal
  Designate/BIND listeners answer for that zone today.

If a hostname is accepted by HAProxy but does not resolve privately to a
provider-network address, HAProxy will reject or fail that request. To route a
new suffix such as `ssh.example.net` privately, the internal DNS layer must
serve that name, or `edge_private_dns_zones` must include a zone that internal
DNS actually answers for.

DNS is split into node-facing and VPS-local services:

- Each OpenStack node runs a dedicated `unbound-vps-edge` service on its
  WireGuard IP only.
- Node Unbound forwards `apps.example.net.` to the Designate/BIND listeners:
  - `10.220.0.11@53`
  - `10.220.0.12@53`
  - `10.220.0.13@53`
- The VPS runs a dedicated `unbound-vps-edge-private` service on
  `127.0.0.1:53`.
- HAProxy uses the VPS resolver for dynamic backend resolution.

The node resolvers intentionally allow DNS queries from the VPS WireGuard
addresses, not arbitrary clients.

### Public Proxying

HAProxy is the selected public edge proxy.

`80/tcp`:

- HAProxy runs in HTTP mode.
- It reads the HTTP `Host` header.
- Only domains listed in `edge_public_domains` and their subdomains are
  accepted.
- It resolves the hostname through `127.0.0.1:53`.
- It connects to the resolved provider address on port `80`.

`443/tcp`:

- HAProxy runs in TCP mode.
- It inspects TLS ClientHello SNI.
- Only domains listed in `edge_public_domains` and their subdomains are
  accepted.
- It resolves the SNI hostname through `127.0.0.1:53`.
- It connects to the resolved provider address on port `443`.
- TLS is passthrough; certificates stay on the origin service.

Generic TCP:

- Generic TCP cannot be routed by hostname unless the protocol carries one.
- Use `edge_tcp_forwards` to allocate explicit public ports from the configured
  pool.
- HAProxy renders one TCP frontend/backend per configured forward.

Example:

```yaml
edge_tcp_forwards:
  - name: example-ssh
    hostname: vm1.apps.example.net
    public_port: 31001
    origin_port: 22
    protocol: tcp
```

### SSH

SSH to the VPS itself is separate from HAProxy. If the VPS has `sshd` listening
on public port `22` and the host firewall allows it, SSH to the VPS continues to
work normally.

SSH to private backends through the edge does not work by hostname on the same
public `22/tcp` listener. Plain SSH does not carry a routable hostname like HTTP
Host or TLS SNI, so HAProxy cannot distinguish `vm1.example.net:22` from
`vm2.example.net:22` when both resolve to the same public VPS IP.

Use one of these models for backend SSH:

- Allocate an explicit public TCP port with `edge_tcp_forwards`, for example
  `203.0.113.10:31001 -> vm1.apps.example.net:22`.
- Keep SSH to backends behind a VPN, bastion, or existing access tunnel.
- Use a protocol wrapper that exposes a routable name before forwarding SSH.

The generic TCP port-forward model is the one currently implemented.

Minecraft:

- `edge_minecraft_enabled` exists as a guard, but Minecraft routing is not
  implemented by this HAProxy role.
- Add a Minecraft-aware proxy role before enabling `25565/tcp`.

### OpenStack Host Gatewaying

Each OpenStack edge node acts as a controlled gateway from its WireGuard link to
the provider network.

The `openstack_edge_gateway` role:

- Enables IPv4 forwarding.
- Detects the provider egress interface with `ip route get provider_gateway_ip`
  unless `edge_gateway_provider_interface` is set.
- Loads dedicated nftables tables:
  - `inet vps_edge_gateway`
  - `ip vps_edge_gateway_nat`
- Allows forwarding from the VPS WireGuard link only to `provider_cidr`.
- Drops forwarding from WireGuard to the networks in `edge_management_cidrs`.
- SNATs provider-bound traffic to `edge_node_snat_ips[inventory_hostname]` when
  `edge_gateway_use_snat` is enabled.

No global nftables or iptables rules are flushed by these roles.

## Repository Layout

```text
ansible.cfg
requirements.yml
inventories/production/
  hosts.ini
  files/ssh/
  group_vars/
playbooks/
  discovery.yml
  phase1-wireguard-dns.yml
  phase2-routing.yml
  phase3-public-edge.yml
  phase4-authentik.yml
  phase5-pomerium.yml
  site.yml
  validate.yml
  validate-authentik.yml
roles/
  edge_wireguard/
  openstack_edge_dns/
  openstack_edge_gateway/
  vps_private_resolver/
  vps_edge_routing/
  vps_edge_security/
  vps_edge_proxy/
  vps_edge_firewall/
  vps_authentik/
  vps_pomerium/
docs/
  authentik-identity-broker.md
  pomerium-ssh-access.md
  operator-checklist.md
```

## Authentication

The production inventory uses 1Password-backed SSH public key stubs, but the
paths are variables:

- `edge_vps_ssh_user`
- `edge_vps_ssh_public_key_file`
- `openstack_edge_ssh_user`
- `openstack_edge_ssh_public_key_file`

The included files are public key stubs used by SSH to select the matching
1Password private key. They are not private keys.

Run playbooks with the 1Password SSH agent available:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/discovery.yml
```

The `ansible.cfg` points SSH at the user SSH config and uses `/tmp/ansible-cp`
for short control socket paths.

## Runbook

Install Ansible collections:

```bash
ansible-galaxy collection install -r requirements.yml
```

Run read-only discovery:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/discovery.yml
```

Apply phases one at a time:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase1-wireguard-dns.yml
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase2-routing.yml
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase3-public-edge.yml
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase4-authentik.yml
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/phase5-pomerium.yml
```

Or apply the full site:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/site.yml
```

Validate the deployed edge:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/validate.yml
```

Validate a real app hostname once it has a private provider-network `A` record:

```bash
SSH_AUTH_SOCK=$HOME/.1password/agent.sock ansible-playbook playbooks/validate.yml \
  -e edge_validation_app_hostname=<app>.apps.example.net
```

## Main Options

Shared edge options live in
`inventories/production/group_vars/all/edge.yml`.

| Variable | Current value | Purpose |
| --- | --- | --- |
| `edge_public_domains` | `example.net` | Accepted HTTP Host and TLS SNI suffixes. |
| `edge_private_dns_zones` | `apps.example.net` | Zones forwarded privately over WireGuard. |
| `edge_domain` | first private DNS zone | Backwards-compatible primary zone alias. |
| `provider_cidr` | `10.200.0.0/24` | OpenStack provider network exposed through the edge. |
| `provider_gateway_ip` | `10.200.0.1` | Address used to detect provider egress interfaces. |
| `edge_routing_mode` | `frr` | `frr` for BGP/BFD, `static` for fixed route metrics. |
| `edge_frr_maximum_paths` | number of WireGuard links | ECMP path count rendered in FRR. |
| `edge_vps_loopback_addr` | `10.250.255.1/32` | Stable VPS source/service address rendered into systemd-networkd. |
| `wireguard_links` | three `/30` links | VPS/node point-to-point tunnel definitions. |
| `edge_dns_forwarders` | derived from `wireguard_links` | Node resolvers queried by the VPS resolver. |
| `designate_dns_forwarders` | `10.220.0.11/12/13@53` | Internal Designate/BIND listeners. |
| `edge_node_snat_ips` | `10.200.0.11/12/13` | SNAT source IP per OpenStack edge node. |
| `edge_management_cidrs` | management VLAN CIDRs | Networks blocked from the WireGuard edge path. |
| `edge_tcp_forwards` | `[]` | Explicit generic TCP public-port mappings. |
| `edge_minecraft_enabled` | `false` | Guard for future Minecraft-aware routing. |

VPS-specific options live in
`inventories/production/group_vars/edge_vps/main.yml`.

| Variable | Current value | Purpose |
| --- | --- | --- |
| `edge_vps_public_endpoint` | `ansible_host` by default | Public WireGuard endpoint used by nodes. |
| `edge_proxy_backend` | `haproxy` | Public edge proxy implementation. `nginx` role path still exists as fallback. |
| `edge_security_enabled` | `true` | Manages CrowdSec, CrowdSec bouncers, and Fail2ban on the VPS. |
| `edge_firewall_enabled` | `true` | Enables the VPS firewall role. |
| `edge_firewall_backend` | `ufw` | Uses UFW for production VPS host firewalling. The nftables backend remains available. |
| `edge_vps_ssh_user` | inventory-specific | SSH user for the VPS. |
| `edge_vps_ssh_public_key_file` | inventory-specific | Public key stub used to select a 1Password key. |
| `authentik_enabled` | `true` | Deploys the Authentik Docker Compose stack on the edge VPS. |
| `authentik_domain` | `auth.example.net` | Public Authentik hostname. |
| `authentik_image_tag` | `2026.5` | Authentik server/worker image tag, override with `AUTHENTIK_IMAGE_TAG`. |
| `authentik_haproxy_enabled` | `true` | Enables HAProxy TLS termination for Authentik after a real cert is present. |
| `pomerium_enabled` | `true` | Deploys Pomerium Core native SSH access on the edge VPS. |
| `pomerium_authenticate_domain` | `ssh.example.net` | Public Pomerium authenticate service hostname. |
| `pomerium_ssh_public_port` | `2222` | Public Pomerium native SSH proxy entry point. |
| `pomerium_ssh_routes` | `[]` | Dynamic OpenStack SSH target route list. |

The Authentik identity broker plan and runbook live in
`docs/authentik-identity-broker.md`.

The Pomerium SSH certificate access runbook lives in `docs/pomerium-ssh-access.md`.

OpenStack-node options live in
`inventories/production/group_vars/openstack_edge_nodes/main.yml`.

| Variable | Current value | Purpose |
| --- | --- | --- |
| `openstack_edge_ssh_user` | inventory-specific | SSH user for OpenStack edge nodes. |
| `openstack_edge_ssh_public_key_file` | inventory-specific | Public key stub used to select a 1Password key. |
| `edge_gateway_provider_interface` | auto-detected | Provider egress interface override. |
| `edge_gateway_use_snat` | `true` | Enables provider-bound SNAT from each edge node. |

## Validation Commands

Useful checks on the VPS:

```bash
wg show
dig @127.0.0.1 apps.example.net SOA
dig @10.250.11.2 apps.example.net SOA
vtysh -c 'show bgp summary'
vtysh -c 'show ip bgp'
vtysh -c 'show bfd peers'
ip route show 10.200.0.0/24 proto bgp
haproxy -c -f /etc/haproxy/haproxy.cfg
ss -ltnp | grep -E ':(80|443)\b'
ss -ltnp | grep -E ':(6060|9100)\b'
```

Useful checks on OpenStack nodes:

```bash
systemctl is-active wg-quick@wg-vps
systemctl is-active unbound-vps-edge
systemctl is-active frr
nft list table inet vps_edge_gateway
nft list table ip vps_edge_gateway_nat
vtysh -c 'show bgp summary'
vtysh -c 'show bfd peers'
```

## Metrics

This repository exposes Prometheus scrape endpoints but does not deploy a
Prometheus server.

- `edge_prometheus_exporters` installs node exporter on the VPS edge host only.
- Node exporter binds to `edge_vps_loopback_addr` on the VPS.
- CrowdSec's native Prometheus endpoint binds to `edge_vps_loopback_addr` on the
  VPS.
- The VPS firewall allows the metrics ports only from
  `edge_metrics_scrape_sources`, which defaults to the WireGuard node addresses.

## Safety And Rollback

Safety properties:

- WireGuard private keys are created on targets only and protected with root-only
  permissions.
- HAProxy is the active public proxy; the role disables NGINX when HAProxy is
  selected.
- OpenStack gateway rules are isolated in dedicated nftables tables.
- The production VPS firewall uses UFW and allows public `22/tcp`, `80/tcp`,
  `443/tcp`, the configured WireGuard UDP ports, and BGP/BFD from tunnel peers.
- Management VLAN forwarding is blocked from the WireGuard edge path.

Rollback entry points:

```bash
systemctl stop haproxy
systemctl stop pomerium-compose
systemctl stop wg-quick@wg-os1 wg-quick@wg-os2 wg-quick@wg-os3
systemctl stop wg-quick@wg-vps
systemctl stop vps-edge-gateway-nftables
systemctl stop frr
```

Only remove dedicated tables/services created by this repository. Do not flush
global OpenStack firewall state.

## Known Gaps

- The HAProxy role validates DNS and listener behavior, but full HTTP/SNI
  end-to-end tests require a real app hostname with a private provider-network
  `A` record.
- FRR currently advertises `provider_cidr` from each node as long as FRR is
  running. A future improvement should gate advertisement on an explicit
  provider-network health check.
- The Minecraft-specific proxy is not implemented.
- UDP application exposure is intentionally out of scope for one public IPv4
  address.
