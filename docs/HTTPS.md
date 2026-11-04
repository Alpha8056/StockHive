# HTTPS for nodes

**What it's for:** letting phones use the **live camera** scanner. Phones only
allow the camera on secure (`https://`) pages. Without HTTPS, phone scanning
still works in photo mode (see
[Scanning with your phone](USER-GUIDE.md#scanning-with-your-phone)).

**What stays the same:** every node keeps working over plain `http://`
exactly as before. The kiosk screen, the launcher, its tiles and nightly
backups all keep using HTTP. HTTPS is added next to it.

> Text like `<node-ip>` is a placeholder. Replace it, including the `< >`, with
> your own value (for example, the address on the node's launcher tile).

---

## Contents

1. [How it works](#1-how-it-works)
2. [Before you start](#2-before-you-start)
3. [Create your CA (once)](#3-create-your-ca-once)
4. [Back up the CA](#4-back-up-the-ca)
5. [Give a node its certificate](#5-give-a-node-its-certificate)
6. [Trust the CA on each phone (once per phone)](#6-trust-the-ca-on-each-phone-once-per-phone)
7. [Open the node over HTTPS](#7-open-the-node-over-https)
8. [Renewing, new IP address, and turning it off](#8-renewing-new-ip-address-and-turning-it-off)
9. [What's stored where](#9-whats-stored-where)

---

## 1. How it works

There's no public domain on a home network, so StockHive uses a **local
certificate authority (CA)**: a private "stamp" that only your phones trust.

- You create the CA **once**, on your own computer (not on a node).
- The CA gives each node a **certificate** covering the node's `.local` name
  and its LAN IP address.
- You tell each phone to **trust the CA once**. After that, every node's
  `https://` page opens with no warnings.

It's built to be safe even though your phones trust it:

- The CA can **only** vouch for `.local` names and private home addresses
  (`10.x.x.x`, `172.16.x.x`–`172.31.x.x`, `192.168.x.x`). It can't be used to
  fake a real website like your bank, even if someone stole the CA key.
- The CA's private key is protected by a passphrase and never leaves the
  computer you create it on. Nodes never get it.
- Each node's own private key is created on the node and never leaves it.

---

## 2. Before you start

You need:

- **A "CA machine":** a computer that is **not** a StockHive node. Any of these:
  - a Linux or Mac computer, or
  - Windows with **WSL** (Windows Subsystem for Linux). In PowerShell, run
    `wsl --install`, restart, and open **Ubuntu** from the Start menu.
- **A copy of StockHive on the CA machine:**
  ```bash
  git clone https://github.com/Alpha8056/StockHive.git ~/StockHive
  ```
- **SSH access from the CA machine to each node**, as `root` or as a user with
  passwordless `sudo` (Raspberry Pi OS usually sets this up for its first user). Check with:
  ```bash
  ssh root@<node-ip> true
  ```
  No SSH? There's a [manual way](#without-ssh) that copies files by hand.

**Why not on a node or the launcher?** The CA key must never sit on a node.
If your launcher shares a machine with a node, that machine counts as a node.
The script refuses to create the CA on a node or inside a git folder.

---

## 3. Create your CA (once)

On the CA machine:

```bash
cd ~/StockHive
bash scripts/https-setup.sh ca-create
```

1. It asks for a **passphrase** twice. Pick a strong one and save it in your
   password manager. You'll type it every time you give a node a certificate.
2. It creates the folder `~/stockhive-ca` with two files:
   - `ca.key`: **private**, encrypted with your passphrase. Never copy it to a
     node, never commit it, never send it anywhere.
   - `ca.crt`: **public**. This is what phones trust.
3. It prints a **fingerprint** (a long `AA:BB:CC:…` code). Note it down. You can
   compare it later on the node's Settings page.

---

## 4. Back up the CA

Without the CA you can't renew certificates, so keep a copy:

1. Copy the whole `~/stockhive-ca` folder to a USB stick, or attach both files
   to an entry in your password manager.
2. Keep the passphrase **separate** from that copy (for example, only in the
   password manager).

StockHive's nightly backups don't include the CA. They only download each
node's inventory and photos, and the CA is never on a node.

**If you lose the CA:** nothing breaks right away. Create a new one (step 3),
trust it on each phone (step 6), and give each node a new certificate (step 5).
Remove the old CA from phones afterwards.

---

## 5. Give a node its certificate

On the CA machine, once per node:

```bash
cd ~/StockHive
bash scripts/https-setup.sh issue root@<node-ip>
```

What happens:

1. The node creates its own private key and asks for a certificate covering its
   `.local` name and its current IP address.
2. The CA machine shows the list and asks **Sign it? [y/N]**. Check the address
   is right, then type `y`.
3. Type the **CA passphrase**.
4. The certificate is installed on the node, nginx is checked and reloaded (if
   anything fails it's rolled back, and HTTP is never affected), and the node
   restarts. It ends with **HTTPS is on: https://&lt;node-ip&gt;/**.

Useful options:

| Option | What it does |
|---|---|
| `--ip <address>` | Also cover another private address. Remembered for renewals. |
| `--name <name>.local` | Also cover another `.local` name. Remembered for renewals. |
| `--port 8443` | Use a different HTTPS port (default 443). |
| `--new-key` | Make the node a new private key too. |

On the node, `sudo bash scripts/https-setup.sh status` shows whether HTTPS is
on, the expiry date, the names covered, and the CA fingerprint.

### Without SSH

1. **On the node:**
   `cd ~/StockHive && sudo bash scripts/https-setup.sh node-request > node.csr`
2. Copy `node.csr` to the CA machine (USB stick, `scp`, …).
3. **On the CA machine:** `bash scripts/https-setup.sh sign node.csr`
   This writes `node.crt`.
4. Copy `node.crt` **and** `~/stockhive-ca/ca.crt` to the node. Never `ca.key`.
5. **On the node:**
   `sudo bash scripts/https-setup.sh node-install node.crt ca.crt`

---

## 6. Trust the CA on each phone (once per phone)

The easiest way to get the CA onto a phone is from a node:

1. On the phone, open the node's **Settings** page: `http://<node-ip>/settings`.
2. In the **HTTPS** card, check the **Fingerprint** matches the one from step 3.
3. Tap **Download CA certificate**.

### iPhone / iPad

Use **Safari** for the download (other browsers can't install it).

1. After tapping **Download CA certificate**, tap **Allow**, then **Close**.
2. Open **Settings**. Tap **Profile Downloaded** near the top (or go to
   **General > VPN & Device Management** and tap **StockHive Local CA**).
3. Tap **Install**, enter your passcode, then **Install** again and **Done**.
4. **Important:** go to **Settings > General > About > Certificate Trust
   Settings** and turn on **StockHive Local CA**. Tap **Continue**.
   Without this step, Safari still shows a warning.

### Android

Android needs a screen lock (PIN, pattern or password) to install a CA.

1. After tapping **Download CA certificate**, the file saves to **Downloads**.
2. Open **Settings** and search for **CA certificate**. It's usually under
   **Security & privacy > More security & privacy > Encryption & credentials >
   Install a certificate > CA certificate** (Samsung: **Biometrics and security >
   Other security settings > Install from device storage > CA certificate**).
3. Tap **Install anyway**, confirm your screen lock, and pick
   **StockHive-CA.crt** from Downloads.

Chrome uses it straight away. Firefox needs its own setting turned on
(**Settings > About Firefox**, tap the logo 5 times, then **Settings > Secret
Settings > Use third party CA certificates**).

---

## 7. Open the node over HTTPS

On the phone, open `https://<node-ip>/` (the node's **Settings > HTTPS** card
shows the exact address). There should be no warning, and the camera button
next to the scan boxes now opens the live camera view.

- **Save it:** add that `https://` page to your home screen (see
  [Using it on a phone](USER-GUIDE.md#13-using-it-on-a-phone)).
- **Launcher tiles still open `http://`.** An option for phones to open nodes
  over HTTPS from the launcher is planned. Until then, use your saved
  `https://` shortcut.
- **Use the IP address, not `.local`.** Android often can't look up `.local`
  names. The certificate covers both, so the IP always works.

---

## 8. Renewing, new IP address, and turning it off

**Renew** when the node's **Settings > HTTPS** card says the certificate
expires soon (it warns 30 days ahead; certificates last about 2 years), or
says **this node's address isn't in the certificate** (for example, after the
router gave it a new IP). On the CA machine:

```bash
bash scripts/https-setup.sh renew root@<node-ip>
```

It picks up the node's current IP automatically and keeps any extra `--ip` /
`--name` entries from before. Phones don't need anything new: they trust the
CA, not each certificate.

**Tip:** give each node a fixed address (a "DHCP reservation" in your router)
so its IP never changes.

**Turn HTTPS off** on a node (HTTP keeps working):

```bash
sudo bash scripts/https-setup.sh node-remove
```

The certificate files are kept. `issue` from the CA machine turns it back on.

**Updates:** **Settings > Update** and **Update All** don't touch HTTPS. The
certificate and its settings are stored outside the StockHive folder.

**Re-running `setup.sh`:** keeps HTTPS on. If you change the node's HTTP port
to the same port HTTPS uses, setup leaves HTTPS off and tells you; run `issue`
again with `--port 8443`.

---

## 9. What's stored where

| Where | What | Who can read it |
|---|---|---|
| CA machine: `~/stockhive-ca/ca.key` | CA private key (encrypted) | only you |
| CA machine: `~/stockhive-ca/ca.crt` | CA certificate (public) | anyone |
| Node: `/etc/stockhive/tls/node.key` | the node's private key | root only |
| Node: `/etc/stockhive/tls/node.crt` | the node's certificate | anyone |
| Node: `/etc/stockhive/tls/ca.crt` | copy of the public CA certificate (served at `/ca.crt`) | anyone |
| Node: `/etc/stockhive/tls/https.env` | on/off, port, extra names | anyone |
| Node: `/etc/nginx/sites-available/stockpi-node-https.conf` | the HTTPS web server settings | anyone |

None of these are inside the StockHive folder, so `git pull` never sees them.
The repo's `.gitignore` also blocks certificate and key files as a safety net.

Problems? See [Troubleshooting: HTTPS](TROUBLESHOOTING.md#14-https-problems).
