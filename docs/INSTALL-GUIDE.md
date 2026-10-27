# StockHive Install Guide

This guide takes you from a blank machine to a working **launcher** and your first
**node**. You don't need to know Linux: every command is written out for you to copy.

Allow about 30 minutes. Each machine needs an internet connection during the install.

For everyday use afterwards, see the [User Guide](USER-GUIDE.md). If something goes
wrong, see [Troubleshooting](TROUBLESHOOTING.md).

> Button and menu names are shown in **bold**, exactly as they appear on screen.
> Text like `<machine-ip>` is a placeholder. Replace it, including the `< >`, with your
> own value. `<your-user>` means the username you log in with.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Prepare the machine](#2-prepare-the-machine)
3. [Get a terminal](#3-get-a-terminal)
4. [Install StockHive](#4-install-stockhive)
5. [Open StockHive](#5-open-stockhive)
6. [First things to do](#6-first-things-to-do)
7. [A kiosk screen](#7-a-kiosk-screen)
8. [Updating later](#8-updating-later)

---

## 1. What you need

**A quick reminder of the parts:**
- A **node** is one inventory, for example "Kitchen Inventory". You can have several.
- The **launcher** is the shared home page that finds every node and shows it as a tile.
  You need one.

More detail is in the User Guide's [What StockHive is](USER-GUIDE.md#1-what-stockhive-is).

### Hardware

Any of these works, for the launcher or a node:

| Option | Good for | Notes |
|---|---|---|
| **Raspberry Pi** | A small always-on box, a kitchen screen, the label printer | Use **64-bit** Raspberry Pi OS. |
| **Proxmox container (LXC)** | If you already run Proxmox | Use a **Debian 12** (or newer) template. No Bluetooth, so no label printer. |
| **Any Debian-like Linux box** | An old PC or a virtual machine | Debian 12 or newer, Ubuntu 24.04 or newer, or similar. |

The system must have **Python 3.11 or newer**. The versions listed above do. Older
ones, like Debian 11, don't, and the install will fail.

The **label printer** is optional, and needs a Raspberry Pi with Bluetooth.

### One machine or two?

- **Two machines (recommended):** the launcher on one, and the node on another. Each
  extra node goes on its own machine.
- **One machine (good for trying it out):** the launcher and a node on the same machine.
  The node uses the normal web address, and the launcher uses port `8080`.

You can start with one machine and add more nodes on other machines later.

---

## 2. Prepare the machine

Follow the part that matches your hardware, then go to [3. Get a terminal](#3-get-a-terminal).

### 2a. Raspberry Pi

1. On another computer, install **Raspberry Pi Imager** from the Raspberry Pi website.
2. Put the Pi's SD card in that computer and open Imager.
3. Choose your **Raspberry Pi model**.
4. For the operating system, choose **Raspberry Pi OS Lite (64-bit)**. It's usually under
   **Raspberry Pi OS (other)**. Pick the desktop version instead if this Pi will drive a
   kitchen screen (see [7. A kiosk screen](#7-a-kiosk-screen)).
5. Choose the SD card as the storage.
6. When Imager offers **OS customisation**, set:
   - a **hostname**, for example `stockhive-kitchen`
   - a **username and password**. This is `<your-user>`. Write them down.
   - your **Wi-Fi** name and password, if the Pi won't use a network cable
   - **SSH** turned on, with password login

   The exact labels differ a little between Imager versions.
7. Write the card, put it in the Pi, and switch the Pi on. Give it a few minutes to start
   the first time.

### 2b. Proxmox container (LXC)

**Download the Debian template (once):**

1. In the Proxmox web page, select your storage (often **local**), then **CT Templates**.
2. Tap **Templates**, choose **debian-12-standard** (or newer), and tap **Download**.

**Create the container:**

1. Tap **Create CT** (top right).
2. **General:** set a **Hostname** and a root **Password**. Leaving
   **Unprivileged container** ticked is fine.
3. **Template:** choose the Debian template you downloaded.
4. **Disks:** 8 GB is plenty.
5. **CPU:** 1 or 2 cores. **Memory:** 1024 MB.
6. **Network:** bridge **vmbr0**, and **IPv4: DHCP**.
7. Finish the wizard and start the container.

**Add a normal user (recommended):**

1. Select the container, then **Console**, and log in as `root` with the password you set.
2. Install the tools StockHive's setup needs:
   ```bash
   apt update && apt install -y sudo git
   ```
3. Create your user and let it use `sudo`:
   ```bash
   adduser <your-user>
   usermod -aG sudo <your-user>
   ```
4. Log out (`exit`) and log back in at the Console as `<your-user>`.

You can also install StockHive as `root`. It then lives in `/root/StockHive` instead of
your home folder. If you do, read the note in [4. Install StockHive](#4-install-stockhive)
about how you log in.

### 2c. Any other Debian-like box

Install the operating system as usual, and make sure you have a normal user that can use
`sudo`. If `sudo` isn't installed, log in as root and run `apt install -y sudo`, then
follow steps 3 and 4 of "Add a normal user" above.

---

## 3. Get a terminal

All the install steps are typed into a **terminal** on the machine.

- **From another computer (SSH):** open a terminal (on Windows, **PowerShell**) and type:
  ```bash
  ssh <your-user>@<machine-ip>
  ```
  On a Raspberry Pi, `ssh <your-user>@<hostname>.local` often works too, using the
  hostname you set in Imager. To find `<machine-ip>`, look for the machine in your
  router's list of connected devices.
- **Proxmox:** select the container, then **Console**.

**One rule for every step below:** run `git` commands as your normal user, never with
`sudo`. Only the setup command itself uses `sudo`.

---

## 4. Install StockHive

1. Install git:
   ```bash
   sudo apt update && sudo apt install -y git
   ```
2. Download StockHive into your home folder:
   ```bash
   git clone https://github.com/Alpha8056/StockHive.git ~/StockHive
   cd ~/StockHive
   ```
3. Start the setup:
   ```bash
   sudo bash setup.sh
   ```

**If you're installing as root:** log in as root directly, at the Proxmox **Console**
login prompt or over SSH. Don't use `pct enter` from the Proxmox host, and don't switch
to root with `su`. Setup works out which user to install for from how you logged in.
With `pct enter` there's no login, so it guesses a user named `pi`, which doesn't exist,
and stops partway. After `su`, it installs for the user you logged in as, not root.

### The questions setup asks

Setup asks a few questions. Press **Enter** to accept the default shown in `[brackets]`.

1. **What do you want to install on this machine?**
   - `1` A node
   - `2` The launcher
   - `3` Both, on this same machine
2. **Port:**
   - Installing one thing: **Node port** or **Launcher port**, default `80`. Keep `80`
     unless you know you need something else.
   - Installing both: **Node port** (default `80`), then **Launcher port** (default `8080`).
     The two must be different.
3. **Node only:** **Instance name**, the node's name on its tile (default
   "Kitchen Inventory"), then a **theme** from `1` to `9` (default `1`, Dark). You can
   change both later in the node's **Settings**.
4. **Launcher only:** a **ZIP code** for the weather. It's required, and must be 5 digits.
   Weather comes from the US National Weather Service, so it only works for US locations.
   Outside the US, type any 5 digits; the rest of StockHive works normally.

Setup then installs everything, which takes a few minutes. It's finished when it shows
**Setup complete!** and the address of what it installed.

If it stops with a red **[ERROR]** line, read the message: it says what went wrong. See
[Troubleshooting](TROUBLESHOOTING.md) for common problems.

---

## 5. Open StockHive

1. If you didn't note the address at the end of setup, find it:
   ```bash
   hostname -I
   ```
   The first number, like `192.168.x.x`, is `<machine-ip>`.
2. On any computer or phone on the same network, open a web browser and go to:
   - a **node** or **launcher** on port 80: `http://<machine-ip>/`
   - a **launcher on a shared machine** (port 8080): `http://<machine-ip>:8080/`
3. On the launcher, your node's tile appears by itself, usually within a minute.

If the tile doesn't appear, see
[A node doesn't appear on the launcher](TROUBLESHOOTING.md#2-a-node-doesnt-appear-on-the-launcher).

---

## 6. First things to do

1. **Check the weather location.** On the launcher, open **Settings** and look at the
   **Weather Location** card. Change the ZIP code there if needed.
2. **Give each machine a fixed address.** In your router, reserve the current address for
   each StockHive machine (often called a **DHCP reservation**). This keeps tiles from going
   Offline when the router hands out new addresses. See
   [Troubleshooting](TROUBLESHOOTING.md#3-tile-offline-wrong-node-or-duplicates).
3. **Add another node.** On another machine, follow sections 2 to 4 again and choose `1`
   (a node). It shows up on the launcher by itself.
4. **Set an admin token.** It protects each node's **Update**, **Restart Service** and
   **Shutdown Device** buttons, and lets the launcher's **Update All** update your nodes.
   Follow [Admin token](../README.md#admin-token-updates-restart-shutdown) in the README.
5. **Set up nightly backups.** Each node can download a backup from
   **Tools > Download Backup**, but automatic nightly backups are safer. See
   [nasbackup/README.md](../nasbackup/README.md).
6. **Label printer (optional).** To print barcode labels on a CTP500 printer, see
   [printbridge/README.md](../printbridge/README.md).

---

## 7. A kiosk screen

A kiosk is a screen that always shows the launcher, for example in the kitchen.

StockHive **doesn't install or start a browser** for a kiosk screen. You set up the
screen and its browser yourself, using whatever the device offers (for example, a
tablet's browser, or the desktop version of Raspberry Pi OS with a browser set to open
the launcher).

Once the screen shows the launcher, follow the User Guide's
[kiosk screen](USER-GUIDE.md#12-the-kiosk-screen) section to mark it as the kiosk and set
up night dimming.

---

## 8. Updating later

To get new versions, open a node's or the launcher's **Settings**, go to the **Device**
card, and tap **Update**. Once an admin token is set, the launcher's **Update All**
updates every node.

If an update fails, see
[Update fails: local changes](TROUBLESHOOTING.md#4-update-fails-local-changes).
