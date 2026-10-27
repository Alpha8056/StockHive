# StockHive

Multi-instance inventory system. Run a separate **node** for each thing
you're tracking, such as Kitchen, Tote Storage or Electronic Components. Each node has its own
IP and its own database, and one shared **launcher** page finds them all automatically.
Nodes and the launcher run on a Raspberry Pi, a Proxmox LXC, or any
Debian-like Linux box.

**New to StockHive?** Start with the [User Guide](docs/USER-GUIDE.md).

This is v2, a clean-break rewrite of [StockPi-InfoPanel](https://github.com/Alpha8056/StockPi-InfoPanel).
The old InfoPanel (network/RF/homelab monitoring) has been retired; the only
piece of it worth keeping — weather — has been rebuilt into the launcher.

## Features

- **Scanning:** scan to add (+1) or remove (−1), quantities, zones and shelves (locations)
- **Grocery & Low Stock:** a grocery list, plus low-stock thresholds per item
- **Expiring Soon:** expiration dates, with a list of what's expiring, on each node and on the launcher
- **Item photos**
- **Custom barcodes:** for items without one, with **label printing** on a
  CTP500 thermal printer through [printbridge/](printbridge/)
- **Leftovers:** "Made" and "Use by" dates on custom-barcode labels
- **9 themes:** Dark, Dim, Slate, Ocean, Forest, Ember, High Contrast, Light, Sepia
- **Launcher search:** one search box across every node's items
- **Update All:** update every node from the launcher, protected by an [admin token](#admin-token-updates-restart-shutdown)
- **Nightly backups:** of every node to a NAS or Linux box with [nasbackup/](nasbackup/)

## How it fits together

```
node/       One Flask app per node. Barcode scan, quantities, grocery/part
            list, locations, low-stock thresholds. Broadcasts itself on
            the LAN via mDNS (_stockpi._tcp.local.) with its name and
            theme color, so the launcher finds it with zero config.

launcher/   One Flask app, on its own device. Browses mDNS for nodes and
            shows them as a tile grid (search nodes and items, offline
            greyed out with a last-seen time). Settings > Nodes shows each
            node's build, updates nodes, and removes one permanently.
            Also owns the weather widget (NWS API).
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

For a node, you'll also be asked for an instance name and theme —
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

## Restoring a node

On a new machine, or after a rebuild:
1. Clone the repo and run `sudo ./setup.sh` (see [Install](#install)), choosing **node**.
2. Open the new node, go to **Tools > Restore**, choose a backup `.zip` and upload it.
   That restores the inventory database and item photos, and the admin token
   if the old node had one.

Backups come from **Tools > Download Backup** or the nightly
[nasbackup/](nasbackup/) job. Zips over 50 MB need the steps in
[nasbackup/README.md](nasbackup/README.md#large-backups).

## Repo layout

| Path | What it is |
|---|---|
| [node/](node/) | The inventory app, one per node |
| [launcher/](launcher/) | The launcher: node grid, item search, weather, Update All |
| [printbridge/](printbridge/) | Bluetooth print service for a CTP500 label printer ([README](printbridge/README.md)) |
| [nasbackup/](nasbackup/) | Nightly backups of every node to a NAS or Linux box ([README](nasbackup/README.md)) |
| [common_update.py](common_update.py) | Shared Settings > Update logic (git pull, pip install, restart) |
| [common_security.py](common_security.py) | Shared same-origin check and admin-token hashing |
| [nginx/](nginx/), [systemd/](systemd/) | Deployment config used by `setup.sh` |
