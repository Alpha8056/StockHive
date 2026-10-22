# StockHive Nightly Backups

This runs on a NAS or any always-on Linux box. Every night it asks the
launcher for the node list and downloads each online node's backup zip
(`inventory.db` plus item photos) over HTTP. It doesn't need any mounts on the nodes.

- **Folders:** each node gets its own folder, `<node-name>-<id>/`, holding files named `<node-name>_<YYYY-MM-DD>.zip`.
- **Retention:** it keeps the last `KEEP_DAYS` days (default 14). It never deletes a node's
  newest backup, even if that node has been off for weeks.
- **Safe downloads:** each download goes to a temporary file and is checked (it must be a zip
  containing `inventory.db`) before replacing anything. A failed download
  never overwrites a good backup.
- **Logging:**
  - Each node is logged as `OK`, `SKIP` (offline) or `FAIL`.
  - One failing node doesn't stop the others.
  - The run only reports failure (exit code 1) when a node actually failed. Offline nodes don't count.
  - It logs a `WARNING` when a backup is over 45 MB (see [Large backups](#large-backups)).

**Requirements:** `python3` (3.8+, standard library only, no pip) and systemd.
The machine must be able to reach the launcher and the nodes over the LAN.

## Install

```bash
git clone https://github.com/Alpha8056/StockHive.git
cd StockHive/nasbackup
sudo ./install.sh                     # first run creates /etc/stockhive-backup.conf
sudo nano /etc/stockhive-backup.conf  # set your values (see below)
sudo ./install.sh                     # installs and starts the daily timer
```

The service runs as the user who ran `sudo`, and that user owns `BACKUP_DIR`.

## Configure

`/etc/stockhive-backup.conf` holds your real values. It's outside the repo and is never committed:

| Key | Meaning | Example |
|---|---|---|
| `LAUNCHER_URL` | Launcher address | `http://launcher.local` |
| `BACKUP_DIR` | Where backups go | `/srv/stockhive-backups` |
| `KEEP_DAYS` | Days to keep per node | `14` |
| `RUN_AT` | Daily run time (24-hour) | `02:30` |
| `LOG_FILE` | Optional extra log file | *(blank)* |

After changing `RUN_AT`, run `sudo ./install.sh` again so the timer picks it up.

## Run once by hand

```bash
python3 stockhive_backup.py --config /etc/stockhive-backup.conf --dry-run   # shows what would happen
sudo systemctl start stockhive-backup.service                                # a real run, as the service user
journalctl -u stockhive-backup -n 50 --no-pager                              # the log
ls -R /srv/stockhive-backups                                                 # your BACKUP_DIR
systemctl list-timers stockhive-backup.timer                                 # next scheduled run
```

**No systemd timers?** Some NAS systems don't have them. Use cron instead, for example at 02:30 daily:

```
30 2 * * * /usr/bin/python3 /path/to/StockHive/nasbackup/stockhive_backup.py --config /etc/stockhive-backup.conf >> /path/to/stockhive-backup.log 2>&1
```

## Restore a backup

**Through the node (easiest):** open the node, go to **Tools > Restore**, choose
the `.zip` and upload it. This replaces that node's inventory and photos.

**From the command line:**

```bash
curl -F "dbfile=@Kitchen-Inventory_2026-01-31.zip" http://<node-address>/restore
```

**Onto a new node:** install the node with `setup.sh`, then restore the zip as
above. The zip is the whole node: its database plus photos.

### Large backups

The node's nginx accepts uploads up to **50 MB** (`client_max_body_size 50M`),
so a bigger zip is rejected by Tools > Restore. The script warns from 45 MB.
You have two options.

1. **Raise the limit on the node** you're restoring to:
   ```bash
   sudo sed -i 's/client_max_body_size 50M;/client_max_body_size 200M;/' /etc/nginx/sites-available/stockpi-node.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```
   Running `setup.sh` again resets this to 50 MB. To keep it, change it in the repo's
   `nginx/node.conf` too. The node also rejects any restore that would write more
   than 200 MB in total.

2. **Restore by hand on the node**, which avoids the upload entirely. Copy the zip to the node, then:
   ```bash
   sudo systemctl stop stockpi-node.service
   cd ~/StockHive/node                      # your node folder
   cp inventory.db inventory.db.before-restore
   unzip -o /path/to/backup.zip inventory.db
   unzip -o /path/to/backup.zip 'photos/*' -d static/    # extracts into static/photos/
   sudo systemctl start stockpi-node.service
   ```
