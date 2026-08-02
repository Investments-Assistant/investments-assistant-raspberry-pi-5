# Pi-hole Ad Blocker Setup

Pi-hole acts as an optional DNS sinkhole for **WireGuard VPN clients**. The VPN-only
deployment does not expose it as a LAN-wide service; DNS and the admin UI are restricted
to the VPN/SSH tunnel.

Pi-hole is already configured in `docker-compose.yml`. This guide covers
the router-side setup needed to activate it.

---

## 1. Start Pi-hole via Docker Compose

```bash
cd ~/investments-assistant
docker compose up -d pihole
```

Check it's running:
```bash
docker compose logs pihole
docker exec pihole pihole status
```

Set your Pi-hole web admin password in `.env`:
```
PIHOLE_PASSWORD=your_strong_password
```

---

## 2. Configure VPN Clients to Use Pi-hole as DNS

The client template already sets `DNS = 10.8.0.1`. Keep `AllowedIPs = 10.8.0.0/24`;
the Pi is not an internet gateway.

---

## 3. Access Pi-hole Admin UI

Create an SSH tunnel over the VPN:
```
ssh -L 8080:127.0.0.1:8080 pi@10.8.0.1
```
Then open `http://127.0.0.1:8080/admin` locally.

Default login: the password you set in `.env` as `PIHOLE_PASSWORD`.

---

## 4. Add Extra Blocklists (optional)

In the Pi-hole admin: **Group Management → Adlists**

Recommended lists:
```
https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
https://raw.githubusercontent.com/FadeMind/hosts.extras/master/add.Spam/hosts
https://raw.githubusercontent.com/crazy-max/WindowsSpyBlocker/master/data/hosts/spy.txt
https://raw.githubusercontent.com/Perflyst/PiHoleBlocklist/master/SmartTV-AGH.txt
```

After adding, go to **Tools → Update Gravity** to download the lists.

---

## 5. YouTube Ad Blocking Note

Pi-hole cannot block YouTube ads served from `googlevideo.com` (same domain as content).
For YouTube-specific blocking you need:

- **Browser**: uBlock Origin or SponsorBlock extension
- **App**: Vanced/ReVanced (Android), Vinegar (iOS Safari)
- **TV**: YouTube Premium or sideload an alternative app

Pi-hole does, however, block **pre-roll ads on smart TVs** and **tracking in smart apps**.

---

## 6. Whitelist Broken Sites

If a site stops working, it may be blocked by Pi-hole.
Add it to the whitelist in the admin UI: **Domains → Whitelist**

Or via CLI:
```bash
docker exec pihole pihole -w example.com
```

---

## 7. WireGuard VPN Integration

When connected to your WireGuard VPN from outside your home,
WireGuard clients are configured to use `10.8.0.1` (the Pi's VPN IP) as DNS.
This means Pi-hole blocks ads **on your phone/laptop even when you're away from home**.

---

## Traffic Flow Summary

```
Device → Pi-hole DNS (Pi) → blocked? → sinkhole (no connection)
                          → allowed? → upstream DNS (1.1.1.1) → Internet
```

Your Pi handles DNS for connected VPN clients; content delivery still goes direct to CDNs.
There is **no performance penalty** — DNS queries are tiny and ultra-fast.
