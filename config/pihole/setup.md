# Pi-hole Ad Blocker Setup

Pi-hole acts as a **network-wide DNS sinkhole**: all DNS queries on your LAN go to the Pi,
which blocks ad/tracker domains before they ever reach your devices.
This blocks ads on your phone, TV, YouTube app, smart devices — no browser extension needed.

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

## 2. Configure Your Router to Use Pi-hole as DNS

**Option A (recommended): Set DNS on the router (DHCP server)**

Log into your router admin panel (usually `192.168.1.1`):
- Find DHCP settings → DNS Server
- Set **Primary DNS** to your Pi's LAN IP (e.g. `192.168.1.100`)
- Set **Secondary DNS** to `1.1.1.1` (fallback if Pi is down)
- Save and restart the router

All devices on your network will now use Pi-hole automatically.

**Option B: Set DNS per device**

If you can't change router DNS, manually set DNS on each device:
- Network settings → DNS → `192.168.1.100`

---

## 3. Access Pi-hole Admin UI

From your LAN (or via VPN):
```
http://192.168.1.100:8080/admin
```

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

Your Pi handles all DNS resolution; content delivery still goes direct to CDNs.
There is **no performance penalty** — DNS queries are tiny and ultra-fast.
