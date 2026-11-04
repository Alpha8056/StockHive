# HTTPS for nodes (proposal)

> **Status: proposal only.** Nothing in `setup.sh` or `nginx/` sets this up yet.
> Nodes serve plain HTTP, and phone scanning works over HTTP in **photo** mode.
> This page describes how to add HTTPS by hand so phones get the **live**
> camera view (see [Scanning with your phone](USER-GUIDE.md#scanning-with-your-phone)).

Phone browsers only allow live camera access on secure (`https://`) pages.
On a home LAN there's no public domain, so the simplest approach is a
**local certificate authority (CA)**: you create one CA, use it to sign a
certificate for each node, and tell each phone to trust the CA once.

Throughout, replace the placeholders with your own values:

| Placeholder | Example |
|---|---|
| `<node-name>` | `kitchen` |
| `<node-host>` | the node's mDNS name, e.g. `kitchen.local` |
| `<node-ip>` | the node's LAN address, e.g. `192.168.1.50` |

## 1. Create a local CA (once)

On any Linux machine you trust (keep the CA key off the nodes if you can):

```sh
mkdir -p ~/stockhive-ca && cd ~/stockhive-ca
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout ca.key -out ca.crt -subj "/CN=StockHive Local CA"
chmod 600 ca.key
```

`ca.key` signs certificates: keep it private and never commit it.
`ca.crt` is the public part you install on phones.

[`mkcert`](https://github.com/FiloSottile/mkcert) does the same thing with
fewer commands, if you prefer it.

## 2. Make a certificate for each node

Phones check the name or IP in the address bar against the certificate's
"subject alternative names", so include every address you'll type:

```sh
cd ~/stockhive-ca
openssl req -newkey rsa:2048 -nodes -keyout <node-name>.key \
  -out <node-name>.csr -subj "/CN=<node-host>"
openssl x509 -req -in <node-name>.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -out <node-name>.crt \
  -extfile <(printf "subjectAltName=DNS:<node-host>,IP:<node-ip>\nextendedKeyUsage=serverAuth")
```

Keep the validity at 825 days or less: iPhones reject server certificates
that last longer.

Copy `<node-name>.crt` and `<node-name>.key` to the node, for example to
`/etc/ssl/stockhive/`, and make the key readable by root only (`chmod 600`).

## 3. Add an HTTPS server block to nginx on the node

Keep the existing HTTP block (the kiosk and launcher tiles still use it) and
add a second block next to it in `/etc/nginx/sites-available/stockpi-node.conf`:

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;

    ssl_certificate     /etc/ssl/stockhive/<node-name>.crt;
    ssl_certificate_key /etc/ssl/stockhive/<node-name>.key;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then check and reload: `sudo nginx -t && sudo systemctl reload nginx`.

If a launcher shares the machine and already uses 443, pick another port
(for example `8443`) and open the node at `https://<node-host>:8443/`.

Note: re-running `setup.sh` rewrites this file, so add the block again afterwards.

## 4. Trust the CA on each phone (once per phone)

Send `ca.crt` to the phone (AirDrop, email, or download it from a local
web page). Install only a CA you created yourself.

**iPhone / iPad**
1. Open `ca.crt`. You'll see **Profile Downloaded**.
2. **Settings > General > VPN & Device Management**, tap the StockHive profile, then **Install**.
3. **Settings > General > About > Certificate Trust Settings**, turn on
   **StockHive Local CA**. (Without this step Safari still shows a warning.)

**Android**
1. **Settings > Security & privacy > More security settings > Encryption & credentials >
   Install a certificate > CA certificate** (the exact menu names vary by phone).
2. Accept the warning and pick `ca.crt`.

Then open `https://<node-host>/` on the phone. There should be no warning,
and the camera button opens the live view.

## Why not a self-signed certificate per node?

It works after tapping through a browser warning, but the camera permission
often isn't remembered, the warning comes back, and every node has to be
accepted separately on every phone. One local CA avoids all of that.

## Caveats

- **Changing IPs:** if a node's IP changes, the certificate's IP entry no
  longer matches. Use a DHCP reservation, or open nodes by their `.local` name.
- **Launcher tiles still use HTTP.** Bookmark (or add to the home screen) the
  node's `https://` address on your phone.
- **Kiosk script:** on an `https://` node page, the browser blocks the
  launcher's `http://` kiosk script (night mode / idle return) as mixed
  content. That only matters on the kiosk, which keeps using HTTP.
- **Renewal:** node certificates expire after 825 days. Repeat step 2 and
  reload nginx. The CA itself lasts 10 years.
