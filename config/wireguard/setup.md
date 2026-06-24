# WireGuard VPN Setup — Raspberry Pi 5

WireGuard is a fast, modern VPN built into the Linux kernel.
It gives you cryptographically authenticated access to your investment assistant
from anywhere in the world — and routes your devices through Pi-hole for ad blocking
even when you're away from home.

**Security model:** WireGuard silently drops all packets from unknown peers (no
response, nothing to scan or brute-force). Authentication is your private key —
nobody can connect without it.

---

## 1. Install WireGuard

`scripts/setup.sh` does this automatically. To do it manually:

```bash
sudo apt install -y wireguard wireguard-tools qrencode
```

---

## 2. Generate Server Keys

`setup.sh` generates these automatically in `/etc/wireguard/`. To do it manually:

```bash
sudo mkdir -p /etc/wireguard && sudo chmod 700 /etc/wireguard
sudo bash -c 'umask 077; wg genkey | tee /etc/wireguard/server_private.key \
  | wg pubkey > /etc/wireguard/server_public.key'
```

---

## 3. Generate Pre-Shared Key

The pre-shared key (PSK) is an extra symmetric secret added on top of public-key
authentication — one more factor an attacker would need to compromise.
`setup.sh` generates one automatically:

```bash
sudo bash -c 'umask 077; wg genpsk > /etc/wireguard/preshared.key'
sudo cat /etc/wireguard/preshared.key   # you'll paste this into each [Peer] block
```

You can use the same PSK for all peers, or generate one per peer for stricter isolation.

---

## 4. Create Server Config

```bash
sudo cp config/wireguard/wg0.conf.template /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo nano /etc/wireguard/wg0.conf
```

Fill in:

- `PrivateKey` → `sudo cat /etc/wireguard/server_private.key`
- Each `[Peer]` `PublicKey` → the public key of that device (generated in step 6)
- Each `[Peer]` `PresharedKey` → `sudo cat /etc/wireguard/preshared.key`

If your Pi uses **Wi-Fi** instead of Ethernet, change `eth0` to `wlan0` in the
`PostUp`/`PostDown` lines.

---

## 5. Enable IP Forwarding

`setup.sh` does this automatically. To do it manually:

```bash
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

---

## 6. Start WireGuard

```bash
sudo systemctl enable --now wg-quick@wg0
sudo wg show    # should show the interface and listen port
```

---

## 7. Router Port Forwarding

On your home router, forward **UDP port 51820** → **Pi's LAN IP** (e.g. `192.168.1.100`).

**Do NOT forward ports 80 or 443** — the investment assistant is only accessible
through the VPN. The DOCKER-USER iptables rules (set by setup.sh) enforce this at
the kernel level even if the router is misconfigured.

If your ISP gives you a dynamic public IP, set up a free DDNS service
(e.g. [Duck DNS](https://www.duckdns.org)) and use the hostname in client configs.

---

## 8. Generate Client Key Pairs

For each device (phone, laptop, etc.), generate a key pair on the Pi or on the device:

```bash
# On the Pi — generate keys for your phone
wg genkey | tee phone_private.key | wg pubkey > phone_public.key
cat phone_public.key    # copy this into the [Peer] block in wg0.conf
```

After adding the public key to `/etc/wireguard/wg0.conf`, restart:

```bash
sudo systemctl restart wg-quick@wg0
```

---

## 9. Create Client Config

Copy `config/wireguard/client.conf.template` for each device and fill in:

- `PrivateKey` → the client's private key (e.g. `cat phone_private.key`)
- `PublicKey` → `sudo cat /etc/wireguard/server_public.key`
- `PresharedKey` → `sudo cat /etc/wireguard/preshared.key`
- `Endpoint` → your DDNS hostname or public IP + `:51820`
- `Address` → the VPN IP for this device (10.8.0.2 for phone, 10.8.0.3 for laptop, etc.)

Import the config file into the **WireGuard app** (Android / iOS / Windows / macOS),
or generate a QR code:

```bash
qrencode -t ansiutf8 < phone.conf
```

Scan the QR code with the WireGuard app on your phone.

---

## 10. IoT Device Integration

For devices that support WireGuard (routers, Linux SBCs):

- Generate a key pair, add a `[Peer]` block with a static VPN IP (e.g. `10.8.0.10`)
- Install the WireGuard client on the device

For devices that don't support WireGuard natively (smart TVs, fridges, etc.):

- Put them on a VLAN and route that VLAN through the Pi, or
- Use the Pi as a DNS server only (Pi-hole handles them via router DHCP)

---

## 11. Verify from Your Phone

1. Switch to cellular (not home Wi-Fi)
2. Enable WireGuard on your phone
3. Open `https://10.8.0.1` → your Investment Assistant UI appears
4. `ping 10.8.0.1` should respond

Your assistant is now accessible **only through your VPN, from anywhere in the world**,
with no publicly visible login page.
