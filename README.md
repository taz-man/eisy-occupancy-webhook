# Occupancy

# Occupancy (webhook)

Replacement for the UDI Portal **Occupancy** / **Occupancy V2** node server, which is going away.
Instead of the portal tool, this plugin receives the in/out calls itself through the PG3 webhook
relay on my.isy.io.

## Requirements
* eisy or polisy running **PG3x**.
* **PG3 remote access** configured and active: my.isy.io -> your ISY -> Select tools -> Maintenance -> PG3 remote access. (Configuring it reboots the eisy.)
* Any app that can call a URL when you arrive/leave: Locative (iOS), UD Mobile, Tasker (Android), Shortcuts, a router script...

## Setup
1. Install and start the plugin.
2. Open its configuration page and add your **Devices** (and, optionally, a **Location** for each, e.g. `Home`). Save.
3. A node is created for every device and location. The plugin's overview/configuration page shows a notice per device with two URLs: **IN** and **OUT**. These contain a secret, so treat them like a password.
4. Paste the IN URL into your app's *arrival* trigger and the OUT URL into its *departure* trigger. In Locative: choose GET, enable Trigger on Arrival/Departure.

### Where do I find my URLs again?
The IN/OUT URLs are shown as PG3 **notices**, not saved settings, so closing one only hides it. Nothing is lost.

The notices are published when they are first created or when they change (a device is added, renamed or removed, the *Reply to webhook callers* setting is toggled, or you run **New Secret**). They are **not** recreated on an ordinary plugin restart, so a notice you have closed stays closed.

To show them again at any time, press **Rebuild Nodes** (Discover) on the Occupancy controller node. The URLs themselves do not change unless you run **New Secret**; if you do, every URL changes and you must update them in your apps.

If a notice instead reads "No devices configured", add at least one device in the plugin configuration and save.

## URL format
```
https://my.isy.io/api/eisy/pg3/webhook/noresponse/<uuid>/<slot>?occ=<node address>&tok=<secret>&st=in
```
* `occ` - the device node's address (shown in the notice).
* `tok` - the secret (also accepted as header `X-Occupancy-Token` or `Authorization: Bearer`).
* `st`  - `in` or `out` (also `enter/exit/home/away/1/0/true/false/on/off`). If `st` is absent, Locative's own `trigger=enter|exit` is used.
* Any extra parameters the app appends (latitude, device, ...) are ignored.

The portal endpoint does no authentication; this plugin rejects any call without the right secret.

## Nodes
* **Occupancy Device** - status True/False (`In`/`Out`). Also accepts Set In / Set Out from programs and sends DON/DOF on change.
* **Occupancy Location** - In when any member device is In. Read-only.

## Controller commands
* **Rebuild Nodes** - re-read the device list, re-create nodes and show the URL notices again.
* **New Secret** - generate a new secret. Every URL changes; re-enter them in your apps.

## Migrating from the portal Occupancy node server
New nodes have new addresses, so programs that referenced the old Occupancy nodes need to be pointed at the new ones. Delete the old portal nodes once you are done.

## Notes
* Renaming a device creates a new node and removes the old one. Keep the order of same-named entries stable.
* A call for an unknown device or with a bad secret is logged as a warning and ignored.

## License Notice

This plugin's development referenced the following source(s), whose license is not on the standard permissive allowlist. Review their license terms before relying on or redistributing this plugin:

- [webhooks · GitHub Topics · GitHub](https://github.com/topics/webhooks) -- license: unknown

See LICENSE.md for full third-party attribution.
