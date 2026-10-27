# StockHive User Guide

This guide is for everyday use: scanning things in and out, fixing counts,
keeping a grocery list, and setting up the kitchen screen. You don't need
to know Linux to follow it.

Setting StockHive up for the first time, admin tokens, updates, backups and
the label printer are covered elsewhere:
[README.md](../README.md), [nasbackup/README.md](../nasbackup/README.md) and
[printbridge/README.md](../printbridge/README.md).

> Button and menu names are shown in **bold**, exactly as they appear on screen.
> Addresses like `http://<node-ip>/` mean "the address of your node". Replace
> `<node-ip>` with your node's address (for example, the one shown on its tile).

---

## I want to…

| I want to… | Go to |
|---|---|
| Understand what a "node" and the "launcher" are | [1. What StockHive is](#1-what-stockhive-is) |
| Open one of my inventories | [2. Opening StockHive](#2-opening-stockhive) |
| Add something I just bought | [3. Scanning](#3-scanning) |
| Use something up (take one away) | [3. Scanning](#3-scanning) |
| Add a new zone, like "Snack Cabinet" | [3. Scanning](#3-scanning) |
| Move an item to a different shelf or zone | [4. Moving an item](#4-moving-an-item) |
| Take back a scan I made by mistake | [5. Undo and Recent Changes](#5-undo-and-recent-changes) |
| See what changed recently | [5. Undo and Recent Changes](#5-undo-and-recent-changes) |
| Fix a wrong count | [6. Editing an item](#6-editing-an-item) |
| Add an expiration date, photo or aisle | [6. Editing an item](#6-editing-an-item) |
| Stop an item from going on the grocery list | [6. Editing an item](#6-editing-an-item) |
| Make a second barcode count as the same item | [6. Editing an item](#6-editing-an-item) |
| Print a barcode label | [6. Editing an item](#6-editing-an-item) |
| Track something that has no barcode | [7. Items with no barcode](#7-items-with-no-barcode) |
| Label leftovers with "Made" and "Use by" dates | [7. Items with no barcode](#7-items-with-no-barcode) |
| See what I need to buy | [8. Grocery List](#8-grocery-list) |
| Send the grocery list to my phone | [8. Grocery List](#8-grocery-list) |
| See what's running low or about to expire | [9. Low Stock and Expiring Soon](#9-low-stock-and-expiring-soon) |
| Rename a node, change its colors, or rename "Grocery List" | [10. Settings on a node](#10-settings-on-a-node) |
| Find an item without knowing which node it's on | [11. The launcher](#11-the-launcher) |
| Set the weather location or add birthdays | [11. The launcher](#11-the-launcher) |
| Dim the kitchen screen at night | [12. The kiosk screen](#12-the-kiosk-screen) |
| Put StockHive on my phone's home screen | [13. Using it on a phone](#13-using-it-on-a-phone) |
| Hide, restore or delete a node on the launcher | [14. Managing nodes](#14-managing-nodes) |

---

## 1. What StockHive is

StockHive keeps track of what you have and where it is.
Each separate inventory is called a **node**: for example, "Kitchen Inventory" for
food and "Tote Storage" for the garage. Every node has its own list of items, its
own locations and its own settings.
The **launcher** is one shared home page that finds all your nodes by itself and
shows them as tiles. It also shows the time, the weather, and anything about to expire.

---

## 2. Opening StockHive

**What it's for:** getting to the right inventory.

1. Open the launcher in a web browser. It shows one **tile** per node.
2. Tap a tile to open that node.
3. To get back to the launcher from any node page, tap the **Apps** button in the
   top-left corner.

You can also open a node directly by typing its address, `http://<node-ip>/`.

**Grey tiles:** a grey tile that says **Offline since …** means the launcher can't
reach that node right now. It's usually switched off or disconnected. You can't open
it until it comes back. When it does, the tile turns normal again by itself.

---

## 3. Scanning

**What it's for:** keeping counts right without typing. You scan when you put
something away (**+1**) and when you use something up (**−1**).

### Pick where you are

At the top of the node's home page, the **Location** card shows your zones (for
example Pantry, Fridge, Freezer).

1. Tap the zone you're putting things into.
2. If that zone has shelves, choose the **Shelf #** (1 to 4).

The chosen location shows in the top-right corner as **Location**.

### Add one (+1)

1. Tap the box in the **Scan (+1)** card. It's usually selected already.
2. Scan the barcode (or type it and tap **Scan**).
3. A green banner shows the item and its new count, for example `Milk +1(3)`.
   - Tap **Edit item** on the banner to change anything about it straight away.

**A barcode StockHive hasn't seen before** opens the **Resolve Barcode** page. Pick one:

- **Link to existing item:** use this when it's something you already have, but its
  barcode scans differently (for example a different size or a new label). Choose
  the item, then tap **Link + Add (+1)**. That barcode will count as that item from now on.
- **Create as new item:** type a name, check the zone and shelf, optionally add an
  expiration date, and tap **Create + Add (+1)**.

### Take one away (−1)

1. Tap the box in the **Remove (-1)** card.
2. Scan the barcode and tap **Remove**.
3. A red banner shows the new count, for example `Milk -1(2)`.

If you see **Item not found**, that barcode isn't in this node. Removing never creates items.

### Add a zone

1. Tap **Tools**, then scroll to **Locations**.
2. Type a name (for example `Snack Cabinet`).
3. Choose **Has shelves (shows Shelf 1-4)** or **No shelves**, and tap **Add**.

---

## 4. Moving an item

**What it's for:** keeping the location right when you move something, for example
from the Pantry to the Freezer.

1. On the node's home page, tap **Move Location**.
2. Tap the new zone (and shelf, if it has shelves). **New target** shows where it will go.
3. Scan the item and tap **Scan**.
4. The **Confirm Move** page shows **From** and **To**. Tap **Move**.

Tap **Cancel** or **Back** to leave without moving anything.

---

## 5. Undo and Recent Changes

**What it's for:** fixing a mistake right away, and seeing what happened lately.

### Undo

After you add one, remove one, or correct a count, an **Undo** button appears at the
bottom of the screen for about 10 seconds. Tap it to take that change back.

### Recent Changes

On the **Inventory** page, tap **Recent Changes** to open it. It lists the last 10
changes, newest first. Small tags explain each one:

- **New:** the item was just created.
- **Correction:** the count was fixed on the Edit page.
- **Undo:** a change was taken back, and the original shows **Undone**.
- **On list** / **Off list:** the item's grocery-list switch was turned on or off.
- **Edited:** something else changed, like the name or the date.

---

## 6. Editing an item

**What it's for:** changing anything about an item: its name, count, date, photo and more.

To open it, go to **Inventory**, find the item (use **Search** if needed), and tap
**Edit**. Or tap **Edit item** on the green banner right after scanning.

When you leave the Edit page with **Back** or **Save**, you go back to the same list,
with the same search, at the same item, which flashes briefly.

### The main card

Change what you need, then tap **Save**.

- **Item Name.**
- **Quantity:** for fixing a miscount. This is saved as a correction, so it doesn't
  count as "used" in the item's stats.
- **Include in Grocery List:** a switch. When it's off, the item never goes on the
  grocery list, even when you run out, and it doesn't show under Low Stock either.
  Turning it off also removes the item from the list right away.
  (If you renamed your list, the switch shows that name instead.)
- **Expiration Date (optional):** the item then shows in **Expiring Soon** when the date is near.
  If an item runs out and you later scan it in again, the old date is cleared for you.
- **Aisle (optional):** free text like `A3`, `7` or `Produce`. The grocery list is
  sorted by aisle.
- **Photo (optional):** choose a picture. Tick **Remove current photo** to delete it.

### Other Barcodes

**What it's for:** when the same product has more than one barcode (for example two
package sizes, or a barcode that scans differently from another side).

- To add one, type or scan the extra barcode and tap **Link Barcode**.
- To stop one counting, tap **Remove** next to it.

### Low Stock Threshold

Type a number and tap **Set**. When the count drops to that number or lower, the item
shows under **Low Stock**. `0` means no warning.

### More

- **View Stats:** adds and removes over the last 28 days, and a rough "days left".
- **Print Label:** prints the item's name and barcode on the label printer. The printer
  has to be set up first; see [printbridge/README.md](../printbridge/README.md).
- **Delete Item:** removes the item completely.

---

## 7. Items with no barcode

**What it's for:** tracking things that don't come with a barcode, like homemade food,
bulk goods or spare parts. StockHive makes a barcode for you, and you can print it as a label.

### Create a custom barcode

1. On the node's home page, under the **Scan (+1)** box, tap **No barcode? Create one →**.
2. You get a new code like `SH-KI-00001` and the **Create as new item** form.
3. Type a name, check the zone and shelf, and tap **Create + Add (+1)**.
4. The green banner says **Created:** and offers **Print label** and **Edit item**.
   Tap **Print label** to print it.
5. Stick the label on the item. From now on you scan it like any other barcode.

You can reprint a label any time from the item's Edit page with **Print Label**.

### Leftovers

**What it's for:** labelling leftovers with the day they were made and when to finish them.

1. On the custom barcode form, tick **Leftovers**.
2. Set **Use within** to a number of days (7 is the default).
3. Tap **Create + Add (+1)**, then **Print label**.

The label shows **Made:** (today) and **Use by:** (today plus your days). The item also
shows in **Expiring Soon** as its Use by date gets close.

---

## 8. Grocery List

**What it's for:** a shopping list that fills itself in.

To open it, tap **Grocery & Low Stock** on the node's home page.

### How items get on it

- When an item's count reaches **0**, it's added to the list automatically.
- When you scan it back in, it's taken off automatically.
- Items with **Include in Grocery List** turned off are never added.
  See [6. Editing an item](#6-editing-an-item).

The list is sorted by **aisle**, and each item shows its aisle if it has one.

### Removing an item

Tap **Remove** next to it. This only takes it off the list; the item itself stays
in your inventory.

### Share, export and print

- **Export:** shows the list as plain text. Tap **Download .txt** to save it.
- **Print / PDF:** opens a page you can print, or save as a PDF.
- **Send to your phone with a QR code:** open `http://<node-ip>/share/grocery`.
  It shows a QR code. Point your phone's camera at it to open the list on your phone.
  The address is also shown underneath in case the QR code doesn't work.

---

## 9. Low Stock and Expiring Soon

**What it's for:** warnings about what's running low and what needs using up.

### Low Stock

On the **Grocery & Low Stock** page, the **Low Stock** card shows items whose count is
above 0 but at or below their **Low Stock Threshold**. Items with the grocery-list
switch turned off are left out. Tap **Stats** for an item's history.

### Expiring Soon

The node's home page shows a **N Expiring** button when anything is expired, due today,
or expiring within the next 3 days. It's red if something is already expired or due
today, and yellow otherwise. You can also open it from **Inventory › Expiring Items**.

- Items you've run out of (count 0) are left out.
- **Edit** opens the item to change its date.
- **Dismiss** hides one warning you don't need, for example something you've already
  checked. It moves to the **Dismissed** card.
- **Undismiss** brings it back. Changing an item's date also brings it back automatically.

The launcher shows the same warnings, across all nodes, on its **Expiring Soon** slide.

---

## 10. Settings on a node

**What it's for:** naming the node and making it look and read the way you want.

To open it, go to **Tools**, then **Open Settings**.

- **Instance:** **Name** is the node's name, shown on its tile and pages. **Theme** has
  9 color themes: Dark, Dim, Slate, Ocean, Forest, Ember, High Contrast, Light and Sepia.
  Tap **Save**.
- **Page Labels:** rename the wording to suit what this node tracks. For example, change
  **Grocery List** to "Part List" for a parts node. Tap **Save Labels**.

The other cards (**Advertised Address**, **Launcher**, **Labels & Printer**,
**Admin Token**, **Device**) are for setup and updates. See [README.md](../README.md)
and [printbridge/README.md](../printbridge/README.md).

---

## 11. The launcher

**What it's for:** one place to see everything.

### Search across all nodes

The box says **Search instances or items…**.

- Typing part of a **node name** shows only matching tiles.
- Typing an **item name or barcode** searches every node. If the text matches no node
  names, results appear after a short pause. You can also press **Enter**.
  Results are grouped by node; tap one to open that item's Edit page on its node.
- **Clear** (shown while there's text) empties the box and shows all tiles again.

If a node is offline or slow, the results say so instead of failing. For example,
"Kitchen Inventory didn't respond".

Opening a search result takes you to the node. That page's **Back** goes to the node's
Inventory; use **Apps** to return to the launcher.

### The carousel

The panel at the top changes every 10 seconds. Swipe or tap the dots to change it yourself.

1. **Clock:** time and date.
2. **Expiring Soon:** items expiring within 7 days, from every node.
3. **Today at a Glance:** the weather, plus holidays and your saved dates coming up soon.

### Weather

Below the tiles: hourly forecast, active alerts, tomorrow's forecast, radar, and a
warning when a storm is close. (Weather covers the US only.)

To set your location, open **Settings** (top-right of the launcher). Under
**Weather Location**, type your 5-digit **ZIP Code** and tap **Save**. Until a ZIP is
set, the weather says **Set a ZIP in Settings**.

### Saved Dates

**What it's for:** birthdays, anniversaries and other dates you want reminding of.
They show on **Today at a Glance** from 7 days before.

1. Go to **Settings › Saved Dates**.
2. Type a name (for example `Mom's Birthday`) and pick the date.
3. Leave **Repeats yearly** ticked for birthdays; untick it for a one-off.
4. Tap **Add**.

To change one, edit it and tap **Save**. To remove it, tap **Delete**. One-off dates that
have passed are marked **(passed)**.

---

## 12. The kiosk screen

**What it's for:** a screen that's always on, for example a tablet or small screen in
the kitchen. Kiosk mode turns on two extras: dimming at night, and going back to the
launcher when nobody's using it.

### Mark a browser as the kiosk

Do this **on the kiosk screen itself**:

1. Open the launcher, then **Settings**.
2. Under **Display**, turn on **This device is a kiosk**.

This setting is saved in that browser only. Your phone and other computers aren't
affected. Node pages opened from the launcher's tiles pick it up automatically.

### Night mode

1. Under **Display**, tick **Night mode › Enabled**.
2. Set **Night hours** (for example 22:00 to 06:00).
3. Set **Dim level** (how dark the screen gets).
4. Tap **Save**.

At night the screen dims. A tap wakes it up for about a minute.

### Return to launcher when idle

Set **Return to launcher after** to a number of minutes, or `0` to turn it off, then
tap **Save**. If nobody touches the kiosk for that long, it goes back to the launcher.
It never leaves a page where you've typed something and not saved it yet.

---

## 13. Using it on a phone

**What it's for:** opening StockHive from your phone's home screen in one tap.
Your phone needs to be on the same network as StockHive (for example, your home Wi-Fi).

**iPhone (Safari):**
1. Open the launcher, or a node, in Safari.
2. Tap the **Share** button, then **Add to Home Screen**, then **Add**.

**Android (Chrome):**
1. Open the launcher, or a node, in Chrome.
2. Tap the **⋮** menu, then **Add to Home screen**, then **Add**.

This adds a shortcut to the page. It isn't a separate app: it opens in your browser,
and needs your home network to work.

---

## 14. Managing nodes

**What it's for:** tidying up the launcher, for example hiding a node you've retired.

To open it, go to the launcher's **Settings** and find the **Nodes** card.

- **Remove:** hides the node's tile from the launcher, even if the node comes back online.
  Nothing on the node itself is deleted.
- Removed nodes are listed under **Removed nodes**:
  - **Restore** puts the tile back.
  - **Delete forever** deletes the launcher's record of that node. If the node comes back
    online later, it reappears as a new tile.
- **Settings** opens that node's own Settings page.

The **Update** and **Update All** buttons on this card, and the admin token they need,
are explained in [README.md](../README.md#admin-token-updates-restart-shutdown).
