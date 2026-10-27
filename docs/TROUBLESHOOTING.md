# StockHive Troubleshooting

This guide helps when something isn't working. Each problem lists **what you see**,
the **likely cause**, and the **fix**, in that order.

For everyday use, see the [User Guide](USER-GUIDE.md). For installing, see
[README.md](../README.md#install).

> Button and menu names are shown in **bold**, exactly as they appear on screen.
> Text like `<node-ip>` is a placeholder. Replace it, including the `< >`, with
> your own value (for example, `<node-ip>` with the address on the node's tile).

---

## Problems

| What's wrong | Go to |
|---|---|
| I don't know if StockHive is running, or I need its logs | [1. Is it running?](#1-is-it-running) |
| A node doesn't show up on the launcher | [2. A node doesn't appear on the launcher](#2-a-node-doesnt-appear-on-the-launcher) |
| A tile is stuck on **Offline** | [3. Tile Offline, wrong node, or duplicates](#3-tile-offline-wrong-node-or-duplicates) |
| Tapping a tile opens a different node | [3. Tile Offline, wrong node, or duplicates](#3-tile-offline-wrong-node-or-duplicates) |
| The same items appear twice in the launcher's **Expiring Soon** | [3. Tile Offline, wrong node, or duplicates](#3-tile-offline-wrong-node-or-duplicates) |
| **Update** fails with "local changes would be overwritten" | [4. Update fails: local changes](#4-update-fails-local-changes) |
| A node on the launcher's machine is skipped by **Update All** | [5. A node is skipped by Update All](#5-a-node-is-skipped-by-update-all) |
| Pages hang or stop loading until I restart | [6. Pages hang or stop loading](#6-pages-hang-or-stop-loading) |
| I want to try a test branch, then go back to normal | [7. Testing a branch](#7-testing-a-branch) |
| The label printer doesn't print | [8. Label printer problems](#8-label-printer-problems) |
| **Restore** says the backup is too big | [9. Backup too big to restore](#9-backup-too-big-to-restore) |
| I run StockHive in a Proxmox container (LXC) | [10. Proxmox LXC notes](#10-proxmox-lxc-notes) |
| I forgot the admin token | [11. Forgot the admin token](#11-forgot-the-admin-token) |
| I want to know what re-running `setup.sh` changes | [12. Re-running setup.sh](#12-re-running-setupsh) |

---

## Before you start

Most fixes here need a **terminal** on the machine running StockHive (the Raspberry Pi,
the Proxmox container, or the Linux box):

- **From another computer:** open a terminal (on Windows, **PowerShell**) and type
  `ssh <your-user>@<node-ip>`.
- **On Proxmox:** select the container, then **Console**.

Two rules that prevent most extra trouble:

1. **Run `git` commands as your normal user, never with `sudo`.** If root runs git in
   the StockHive folder, it can leave files only root can change, and later updates fail.
2. **Commands that start with `sudo`** will ask for your password. Nothing shows while
   you type it. That's normal.

The commands below assume you cloned StockHive to `~/StockHive`, as in the README.
If yours is somewhere else, use that folder instead.

---

## 1. Is it running?

Each machine runs one or two StockHive services:

| Service | What it is |
|---|---|
| `stockpi-node` | A node (an inventory) |
| `stockpi-launcher` | The launcher |

**Check a service:**

1. Run one of these:
   ```bash
   systemctl status stockpi-node
   systemctl status stockpi-launcher
   ```
2. Look at the **Active:** line:
   - `active (running)`: it's running. If the page still doesn't load, read its log (below).
   - `failed` or `activating (auto-restart)`: it's crashing. Read its log.
   - `could not be found`: it isn't installed on this machine.
3. Press **q** to get back to the prompt.

**Read the log** (the most recent 100 lines):

```bash
journalctl -u stockpi-node -n 100 --no-pager
```

Use `stockpi-launcher` instead for the launcher. To watch new lines as they
happen, use `journalctl -u stockpi-node -f`, and press **Ctrl+C** to stop.

**"502 Bad Gateway" in the browser** means the web server (nginx) is fine but the
StockHive service behind it isn't answering. Check the service as above. nginx's own
errors are in:

```bash
sudo tail -n 50 /var/log/nginx/error.log
```

**Restart a service:**

- **From the browser:** open **Settings**, go to the **Device** card, and tap **Restart Service**.
- **From a terminal:**
  ```bash
  sudo systemctl restart stockpi-node
  sudo systemctl restart stockpi-launcher
  ```

---

## 2. A node doesn't appear on the launcher

**What you see:** the node works when you type its address, `http://<node-ip>/`, but it
has no tile on the launcher.

**How discovery works:** each node announces itself on your network using **mDNS**,
and the launcher listens for it. StockHive does this itself. It does **not** need
Avahi or any other extra software installed.

**Likely causes and fixes:**

1. **The node was removed.** Open the launcher's **Settings**, find the **Nodes** card,
   and look under **Removed nodes**. Tap **Restore** if it's there.
2. **They're on different networks.** mDNS only works inside one network. It doesn't
   cross between subnets or VLANs, and guest Wi-Fi often blocks it. Put the node and
   the launcher on the same network.
3. **A firewall is blocking mDNS.** mDNS uses **UDP port 5353**. If you turned on a
   firewall (for example `ufw`) on either machine, allow it:
   ```bash
   sudo ufw allow 5353/udp
   ```
4. **Check it from both sides:**
   - On the node, open **Settings** and look at the **Launcher** card. If it says
     **not found yet**, the node can't hear the launcher either, which points to the
     network (causes 2 and 3), not to StockHive.
   - On the launcher, open `http://<launcher-ip>/debug/mdns` (add the port, like `:8080`,
     if your launcher isn't on port 80). `known_nodes` lists every node the launcher has
     heard from.
   - Optional: if you have Avahi's tools installed (`sudo apt install avahi-utils`), run
     `avahi-browse -rt _stockpi._tcp` to list every node announcing itself.
5. **You open the launcher over a VPN like Tailscale.** The node announces its home
   network address, which you can't reach from outside the house. On the node, open
   **Settings**, go to the **Advertised Address** card, type the node's Tailscale IP
   in **Manual override**, and tap **Save**.

---

## 3. Tile Offline, wrong node, or duplicates

### The tile says Offline but the node is on

**What you see:** the node's tile is grey and says **Offline since …**, but the node works
at `http://<node-ip>/`.

**Likely cause:** the node's address changed (for example, your router gave it a new one).
A node only works out its address when it starts, so it keeps announcing the old one.

**Fix:**

1. Restart the node (see [1. Is it running?](#1-is-it-running)). The tile updates within a
   minute or two.
2. To stop it happening again, give the node a fixed address in your router
   (often called a **DHCP reservation**).

A node that really is switched off can take up to about **4 minutes** to turn grey,
and turns normal again by itself when it's back.

### A tile opens the wrong node, or Expiring Soon shows duplicates

**What you see:** one or more of these:
- tapping a tile opens a different node, or the launcher itself
- the launcher's **Expiring Soon** shows the same items twice, under different node names
- a tile stays **Online** even though that node is switched off

**Likely cause:** the node is announcing itself as `127.0.0.1` (also called **localhost**),
which means "this same machine". That happens when the node starts before its network is
ready. When the launcher follows `127.0.0.1`, it reaches whatever runs on **the launcher's
own machine**, such as a second node installed there. So the tile opens that node, and
its expiring items are counted twice.

**Fix:**

1. On the affected node, open **Settings** (type `http://<node-ip>/settings` if its tile
   doesn't open it) and find the **Advertised Address** card.
2. Read **Currently broadcasting**. If it shows `127.0.0.1`, restart the node
   (see [1. Is it running?](#1-is-it-running)). It detects its address again on start.
3. If it keeps happening after reboots, type the node's real address in **Manual override**
   and tap **Save**. Give the node a fixed address in your router first, so this stays right.

### Two machines show up as one tile

**What you see:** one tile that keeps switching between two different machines.

**Likely cause:** a backup from one node was restored onto a second machine while the first
one is still running. A backup includes the node's identity (its hidden ID, name and theme),
so both machines now claim to be the same node.

**Fix:** never restore a node's backup onto a second node while the original is still running.
Restore is meant for replacing a node. If it already happened, switch one of the two machines
off. Re-running `setup.sh` doesn't fix this, because it keeps the restored identity.

**After any restore, restart the node** (see [1. Is it running?](#1-is-it-running)). Until it
restarts, the node keeps announcing its old identity to the launcher.

---

## 4. Update fails: local changes

**What you see:** after tapping **Update**, a red message like:

```
Update failed: error: Your local changes to the following files would be overwritten by merge:
        setup.sh
```

**Likely cause:** a file in the StockHive folder differs from the official copy. Often only
its "can run" permission changed, for example after `chmod +x setup.sh` from the README.

**Fix:**

1. Open a terminal on that machine and go to the StockHive folder:
   ```bash
   cd ~/StockHive
   ```
2. See what's changed:
   ```bash
   git status
   ```
3. Tell git to ignore permission-only changes:
   ```bash
   git config core.fileMode false
   ```
4. If `setup.sh` is still listed by `git status`, put back the official copy. This discards
   any edits you made to that file:
   ```bash
   git checkout -- setup.sh
   ```
   Do the same for any other file it lists that you don't need to keep.
5. Run `git status` again. It should say **nothing to commit, working tree clean**.
6. Tap **Update** again.

---

## 5. A node is skipped by Update All

**What you see:** on the launcher's **Settings > Nodes**, **Update All** shows
**Updates with the launcher, skipped** for one node.

**Likely cause:** that node is installed on the same machine as the launcher, and they
share one copy of the code. That's expected, not an error.

**Fix:** update the launcher itself. In the launcher's **Settings**, go to the **Device**
card and tap **Update**. It downloads the new code once and restarts both the launcher and
that node.

---

## 6. Pages hang or stop loading

**What you see:** pages spin forever or stop loading, and only a restart brings them back.
It's often right after an update, a reboot, or a fresh install.

**Likely cause:** an older StockHive service file. Newer versions of the web server
StockHive uses (gunicorn) create a "control socket" that can hang on these machines.
StockHive's service files now turn it off with `--no-control-socket`, but **Update doesn't
replace service files**. A machine installed before that fix still has the old one.

**Check:**

```bash
systemctl cat stockpi-node | grep -c no-control-socket
systemctl cat stockpi-launcher | grep -c no-control-socket
```

`1` means the fix is installed. `0` means it isn't. Only check the services this machine
runs (see [1. Is it running?](#1-is-it-running)).

**Fix:** re-run `setup.sh`, which installs the current service files. Read
[12. Re-running setup.sh](#12-re-running-setupsh) first.

**A shorter pause (up to 30 seconds):** each StockHive app handles one request at a time.
One slow request (for example, the launcher waiting on a node that's switched off, or a big
photo upload) makes other pages wait until it finishes or hits the 30-second limit. Look for
`WORKER TIMEOUT` in the log (see [1. Is it running?](#1-is-it-running)). If it keeps
happening, check for nodes that are off or unreachable (see
[3. Tile Offline, wrong node, or duplicates](#3-tile-offline-wrong-node-or-duplicates)).

---

## 7. Testing a branch

Use this to try a change on a **test node** before it reaches your main code (the `main`
branch). Do it on a test machine, not the node you rely on every day.

**Before you start:** take a backup from the node's **Tools > Download Backup**. A test
branch can add things to the database that stay there after you switch back.

**Switch to a branch:**

1. Open a terminal on the test machine and go to the StockHive folder:
   ```bash
   cd ~/StockHive
   ```
2. Download the latest branches and switch:
   ```bash
   git fetch origin
   git checkout <branch-name>
   ```
3. Install anything new the branch needs:
   ```bash
   node/venv/bin/pip install -r node/requirements.txt
   ```
   On a launcher, use `launcher/venv/bin/pip install -r launcher/requirements.txt` instead.
4. Restart the service (see [1. Is it running?](#1-is-it-running)). If this machine runs
   both a node and the launcher, restart both: they share the same code.

While you're on a branch, **Update** pulls new changes from **that branch**, not from `main`.

**Switch back to main:**

1. In the StockHive folder:
   ```bash
   git checkout main
   git pull --ff-only
   ```
2. Repeat steps 3 and 4 above.

If `git checkout` refuses because of local changes, see
[4. Update fails: local changes](#4-update-fails-local-changes).

---

## 8. Label printer problems

Pairing, testing and fixing the label printer are covered in
[printbridge/README.md](../printbridge/README.md), including its
[Troubleshooting](../printbridge/README.md#troubleshooting) section.

---

## 9. Backup too big to restore

If **Tools > Restore** rejects a backup for being too large, follow
[Large backups](../nasbackup/README.md#large-backups) in nasbackup/README.md.

---

## 10. Proxmox LXC notes

StockHive nodes and the launcher run fine in a Proxmox container. Two things don't work
out of the box:

**Label printing (Bluetooth):** containers don't get Bluetooth, so the print bridge can't run
in one. Run the print bridge on a Raspberry Pi with Bluetooth, near the printer. See
[printbridge/README.md](../printbridge/README.md).

**Tailscale:** Tailscale needs the container to have the `/dev/net/tun` device.

1. On the **Proxmox host** (not inside the container), open the container's config. Replace
   `<ctid>` with the container's ID number, shown next to its name in Proxmox:
   ```bash
   nano /etc/pve/lxc/<ctid>.conf
   ```
2. Add these two lines at the end:
   ```
   lxc.cgroup2.devices.allow: c 10:200 rwm
   lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
   ```
3. Save (**Ctrl+O**, **Enter**), exit (**Ctrl+X**), and restart the container:
   ```bash
   pct reboot <ctid>
   ```
4. Install Tailscale inside the container as usual.
5. If you'll open the launcher over Tailscale, set each node's **Advertised Address**
   (see cause 5 in [2. A node doesn't appear on the launcher](#2-a-node-doesnt-appear-on-the-launcher)).

---

## 11. Forgot the admin token

See **Forgot the token?** in the README's
[Admin token](../README.md#admin-token-updates-restart-shutdown) section.

---

## 12. Re-running setup.sh

Re-running `setup.sh` is the way to install new service files and web server settings,
which **Update** doesn't touch. It keeps your inventory, photos and settings database,
but it **does** change some things. Know these before you run it:

- **It asks all its questions again.** Choose the same option as before (node, launcher, or
  both) and the **same port numbers**, or the node's address changes.
- **The node's name and theme are reset** to what you type, and the default is
  "Kitchen Inventory" with the Dark theme. Type the current name and pick the current theme.
  (Page Labels, like a renamed "Grocery List", are kept.)
- **It removes every other website enabled in nginx** on that machine. It assumes the
  machine is only for StockHive.
- **The node's upload limit goes back to 50 MB.** If you raised it for
  [large backups](../nasbackup/README.md#large-backups), do that step again afterwards.
- **The launcher's weather ZIP is kept.** It asks for one, but keeps the existing setting.
  Change it from the launcher's **Settings** if needed.
- **It re-creates the service files, nginx settings and sudo permissions** for StockHive,
  replacing any changes you made to them by hand.

**To re-run it:**

```bash
cd ~/StockHive
sudo ./setup.sh
```
