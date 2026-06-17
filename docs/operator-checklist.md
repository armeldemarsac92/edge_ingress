# Operator Checklist

## Before Applying Roles

- Confirm SSH access to the VPS and all three OpenStack hosts.
- Replace inventory placeholders for `edge1` and `edge_vps_public_endpoint`.
- Reserve one host-owned provider-network SNAT address per OpenStack node.
- Confirm whether each reserved SNAT address can safely live on the provider
  bridge/interface, especially in the Kolla/OVN layout.
- Confirm generated WireGuard private keys exist only on the targets under
  `/etc/wireguard/keys`.
- Fill `edge_node_snat_ips` only after the addresses are reserved and safe.
- Add explicit `edge_tcp_forwards` entries only for services that should be
  publicly reachable through allocated TCP ports.
- Confirm the intended VPS firewall backend and public ports before enabling
  `edge_firewall_enabled`.
- Confirm Ansible reaches the VPS as a non-root admin user. The security role
  disables direct root SSH and preserves then removes `/root/.ssh/authorized_keys`
  by default.
- If the admin SSH or OpenStack WireGuard peer public source addresses are stable,
  set `EDGE_ADMIN_SSH_SOURCES` and `EDGE_WIREGUARD_PUBLIC_SOURCES` as comma-separated
  CIDR/IP allowlists before applying the firewall role. Set
  `EDGE_FIREWALL_REQUIRE_ADMIN_SSH_SOURCES=true` or
  `EDGE_FIREWALL_REQUIRE_WIREGUARD_SOURCES=true` only after confirming those
  allowlists are complete.
- To let the playbook manage HAProxy ACME certificates, set `EDGE_ACME_ENABLED`,
  `EDGE_ACME_EMAIL`, `EDGE_ACME_AUTHENTICATOR`, and any DNS plugin package/argument
  variables required by the provider, such as `EDGE_ACME_CERTBOT_PLUGIN_PACKAGES`
  and `EDGE_ACME_EXTRA_ARGS`.

## Validation

Run discovery:

```bash
ansible-playbook playbooks/discovery.yml
```

Run syntax checks:

```bash
ansible-playbook --syntax-check playbooks/site.yml
```

Phase 1 success criteria:

```bash
ansible-playbook playbooks/validate.yml
networkctl status lo
dig @127.0.0.1 apps.example.net SOA
dig @10.250.11.2 apps.example.net SOA
wg show
```

When a real app hostname exists, validate its private provider address with:

```bash
ansible-playbook playbooks/validate.yml -e edge_validation_app_hostname=<app>.apps.example.net
dig @127.0.0.1 <app>.apps.example.net A
```

Phase 2 success criteria:

```bash
ip route get <provider-address>
ip route show <provider-cidr> proto bgp
vtysh -c 'show bgp summary'
vtysh -c 'show bfd peers'
nft list table inet vps_edge_gateway
```

Phase 3 success criteria:

```bash
curl -H 'Host: app.apps.example.net' http://<vps_public_ip>/
openssl s_client -connect <vps_public_ip>:443 -servername app.apps.example.net </dev/null
nc -vz <vps_public_ip> 31001
```

Metrics success criteria:

```bash
ss -ltnp | grep -E ':(6060|9100)\b'
curl -fsS http://<wireguard-metrics-ip>:9100/metrics >/dev/null
curl -fsS http://<vps-wireguard-loopback-ip>:6060/metrics >/dev/null
```

Host hardening checks:

```bash
sshd -T | grep -E 'permitrootlogin|passwordauthentication|kbdinteractiveauthentication'
test ! -e /root/.ssh/authorized_keys
systemctl is-active auditd
auditctl -s
```

Certificate automation checks:

```bash
systemctl list-timers '*cert*'
certbot renew --dry-run
openssl x509 -in /etc/haproxy/certs/auth.example.net.pem -noout -dates
```

## Rollback

- Stop the public proxy first.
- Disable only the `vps-edge-*` systemd services created by these roles.
- Delete only nftables tables named `vps_edge_*`.
- Stop WireGuard interfaces with `systemctl stop wg-quick@<interface>`.
- Leave existing Cloudflare/private-route access in place until each migrated
  service has passed validation.
