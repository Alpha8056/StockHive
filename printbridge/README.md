# StockHive Print Bridge

A small service for the kitchen Pi (the one with Bluetooth). It accepts
label images from StockHive nodes over HTTP and prints them on a
Core Innovations CTP500 over Bluetooth.

- Listens on port **8765** (its own port; it doesn't touch nginx, the
  launcher or the kiosk).
- Every print needs the shared token, so other devices on the LAN can't print.
- It connects to the printer only while a label is printing and disconnects
  afterwards. It runs at low priority with a 100 MB memory cap.

## 1. Pair the printer

Turn the CTP500 on, then on the Pi:

```bash
bluetoothctl
  power on
  agent on
  default-agent
  scan on            # wait for a device named "CorePrint..." and note its MAC
  scan off
  pair AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  quit
```

If it asks for a PIN, try `0000`. Don't run `connect` in bluetoothctl.
The bridge opens its own serial (RFCOMM) link for each print.

## 2. Install

From the repo clone on the Pi (adjust the path if your clone is elsewhere):

```bash
cd ~/StockHive/printbridge
python3 -m venv venv
venv/bin/pip install -r requirements.txt

sudo cp printbridge.env.example /etc/stockhive-printbridge.env
sudo chmod 600 /etc/stockhive-printbridge.env
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # new token
sudo nano /etc/stockhive-printbridge.env                          # set PRINTER_MAC and TOKEN
```

## 3. Test before installing the service

Print directly over Bluetooth, with no HTTP involved:

```bash
PRINTER_MAC=AA:BB:CC:DD:EE:FF venv/bin/python bridge.py --selftest
```

You should get a bordered strip with "self-test OK" and a row of bars.

## 4. Run it as a service

```bash
sed -e "s|User=kinv|User=$USER|" \
    -e "s|/home/kinv/StockHive/printbridge|$PWD|g" \
    stockhive-printbridge.service | sudo tee /etc/systemd/system/stockhive-printbridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now stockhive-printbridge.service
journalctl -u stockhive-printbridge -f      # logs
```

Test over HTTP from any machine on the LAN (any PNG works; it's scaled to 384 dots wide):

```bash
curl http://kitchen-pi.local:8765/health
curl -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: image/png" \
     --data-binary @label.png http://kitchen-pi.local:8765/print
```

Then in StockHive, go to **Settings > Labels & Printer**. Set the bridge address
to `http://<pi-ip-or-hostname>:8765`, paste the token, Save, and press **Test Print**.

## Troubleshooting

**`PermissionError` / "Printer not reachable (PermissionError)"**: the
service user isn't allowed to open Bluetooth sockets. Add it to the
`bluetooth` group and restart the service (group changes only apply to
newly started processes):

```bash
sudo usermod -aG bluetooth $USER
sudo systemctl restart stockhive-printbridge.service
```

If you ran `--selftest` by hand, log out and back in (or reboot) first so
your shell picks up the new group.

**"Printer not reachable (TimeoutError)" or (ConnectionRefusedError)**: the
printer is off, asleep or out of range, or it isn't paired/trusted.
Check `bluetoothctl info AA:BB:CC:DD:EE:FF` shows `Paired: yes` and `Trusted: yes`.

**401 Bad token**: the token in StockHive Settings doesn't match `TOKEN` in
`/etc/stockhive-printbridge.env`.

**Anything else in the env file changed**: run `sudo systemctl restart stockhive-printbridge.service`.
