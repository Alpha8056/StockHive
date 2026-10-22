# StockHive
Stable: abfc566d5132dee01353ba6cebba740d4deeae46

Multi-instance inventory system for Raspberry Pi. Run a separate StockPi
**node** on each Pi — Kitchen, Tote Storage, Electronic Components, whatever
you're tracking — each with its own IP and its own database, and find all of
them automatically from one shared **launcher** page.

This is v2, a clean-break rewrite of [StockPi-InfoPanel](https://github.com/Alpha8056/StockPi-InfoPanel).
The old InfoPanel (network/RF/homelab monitoring) has been retired; the only
piece of it worth keeping — weather — has been rebuilt into the launcher.

## How it fits together

```
node/       One Flask app per Pi. Barcode scan, quantities, grocery/part
            list, locations, low-stock thresholds. Broadcasts itself on
            the LAN via mDNS (_stockpi._tcp.local.) with its name and
            theme color, so the launcher finds it with zero config.

launcher/   One Flask app, on its own device. Browses mDNS for nodes and
            shows them as a tile grid (search/filter, offline greyed out
            with a last-seen time, Settings > Manage Nodes to remove one
            permanently). Also owns the weather widget (NWS API).
```

Each node is reachable directly at `http://<its-ip>/` — there's no longer a
single box path-routing between apps like in v1. mDNS/Avahi (`.local`
hostnames) means nothing needs a static IP or router configuration.

## Install

Same script everywhere — it asks what this machine should run:
```bash
git clone <this-repo> ~/StockHive && cd ~/StockHive
chmod +x setup.sh && sudo ./setup.sh
```
Choose **node**, **launcher**, or **both**. If you pick both on the same
machine (handy for testing without a second Pi/LXC), it'll ask for two
different ports — nginx can't have both apps answer on 80 at once — and
defaults to node on 80 / launcher on 8080.

For a node, you'll also be asked for an instance name and theme color —
both are editable later, live, from that node's `/settings` page (Settings
> Page Labels also lets you rename wording like "Grocery List" to fit
whatever the node is tracking, e.g. "Part List" for a tote-storage
instance). For the launcher, you'll be asked for a ZIP code for the
weather widget.

## Admin token (updates, restart, shutdown)

Each node's **Update**, **Restart Service** and **Shutdown Device** buttons,
and the launcher's **Update All**, are protected by an admin token:

- **Without a token:** a node's own buttons work as before, but only from that node's
  own pages. Another website can't trigger them through your browser.
  The launcher can't update the node remotely.
- **With a token set:** the token is required. You type it into the
  Device card on the node, or enter it on the launcher's Nodes card. Scripts
  elsewhere on the network can no longer update, restart or shut a node down.

**Use a long random token, not a memorable password.** Let a password
manager generate one (24+ characters). It also fills the token
field for you. Or generate one on any machine:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```
Use the **same token on every node**. The node stores only a salted hash,
and the launcher doesn't store it at all.

**Setting it up (one time):**
1. Update every node and the launcher the usual way (Settings > Update on each).
2. On each node, go to **Settings > Admin Token**, paste the token and click **Set Token**.
3. On the launcher, go to **Settings > Nodes**, enter the token, and then use **Update** or
   **Update All**. Nodes are updated one at a time. A node that shares one machine
   with the launcher updates with the launcher's own Update button instead.

**Forgot the token?** On that node's machine, clear it (the buttons then
work without a token again):
```bash
cd ~/StockHive/node    # your node folder
venv/bin/python -c "import db; db.set_setting('admin_token_hash', '')"
```

**Restoring a backup** from another node also brings that node's token,
because the token hash is stored in the inventory database.

## Repo layout

See [node/](node/), [launcher/](launcher/), [nginx/](nginx/) and
[systemd/](systemd/) for the two apps and their deployment config.
