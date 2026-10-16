# Yealink T31P setup

Below, `<phone-ip>` is the phone's address (press **OK** when idle to see it) and `<mac-ip>` is the Mac's
(`phone status` prints it; `lan_ip` in config.toml pins it).

## 1. DHCP reservation (on the router)

Recommended. The Mac's address matters most: the phones register to it and Asterisk binds to it.
Reserve it on the router (its MAC is in `ifconfig en0 | grep ether`), then pin it in `~/.claude-phone/config.toml`
as `lan_ip`. `phone status` and the daemon log warn if it changes. Reserving the phone's address too is optional.

## 2. Factory reset

Worth doing on a second-hand phone, which may still point at an old provider's provisioning server.

1. With the phone idle, press and hold **OK** for about 10 seconds until it asks "Reset to factory?". Press **OK**.
   (Or: Menu → Settings → Advanced Settings, password `admin` → Reset Config → Reset to Factory.)
2. Wait for it to reboot (2-3 minutes). Check its IP: press **OK** once when idle. Note it as `<phone-ip>`.

## 3. Web admin

1. Open **https://\<phone-ip\>** and accept the self-signed certificate warning. Port 80 is off.
2. Log in as `admin` / `admin`. Set a new admin password when asked (Security → Password if not).

## 4. Stop old provisioning

**Settings → Auto Provision**
- PNP Active: **Off**
- DHCP Active: **Off**
- Server URL: **empty**
- Confirm.

## 5. Register as extension 200

Get the SIP password on the Mac: `phone secret sip`

**Account → Register → Account 1**
| Field | Value |
|---|---|
| Line Active | Enabled |
| Label | Claude |
| Display Name | your name |
| Register Name | 200 |
| User Name | 200 |
| Password | *(from `phone secret sip`)* |
| SIP Server 1 | `<mac-ip>`, port 5060 |
| Transport | UDP |
| Outbound Proxy | Disabled |

Confirm. The status should change to **Registered** within a few seconds. On the Mac, `phone status` should list contact `200/sip:200@<phone-ip>`.

## 6. Codecs (G.722 first)

**Account → Codec → Account 1**: enabled list in this order: **G722, PCMU, PCMA**. Move everything else to Disabled. Confirm.

## 7. Message key and light

**Account → Advanced → Account 1**
- Voice Mail: **500**
- Subscribe for MWI: **Disabled** (Asterisk sends unsolicited notifications; this is the default)

Confirm. The power LED flashes red when there are questions waiting.

## 8. Time

**Settings → Time & Date**: your Time Zone, NTP server `pool.ntp.org`. Confirm.

## 9. Test

1. Lift the handset and dial **600**: you should hear yourself (echo test).
2. On the Mac, set `ring_ext = "200"` in `~/.claude-phone/config.toml` (remove the line; 200 is the default) and run `phone restart daemon`.
3. Dial **500**: you hear the voicemail menu.

## Notes

- **DND:** the phone rejects calls with code 480. `call_<name>` treats that as "not answered" and leaves a voicemail.
- **Caller ID length:** the T31P shows 17 characters, then scrolls the rest. `callerid_max` (17) trims the branch first, then the repo.

## 10. Live session list

**Claude soft key (live, use this).** The phone fetches the list on every press.
**Dsskey → Programmable Key**: on the soft key that was Directory, set Type **XML Browser**, Label `Claude`,
Value `http://<mac-ip>:8766/sessions.xml`. Confirm. Press it, pick a session, press Select (or OK): the Mac
rings the phone straight back on the Claude account, picked up on the speaker, and puts you through. It
works the same when the Claude account isn't the phone's first or default one, which is what a selected
entry would otherwise be dialled on. The Claude account's line key also dials the highlighted entry.

**Directory phonebook (cached).** Remote phonebooks refresh at most hourly, and nested folders are
prefetched with the top level, so this list goes stale. Kept for completeness (Menu → Directory → Claude):
**Directory → Remote Phone Book**: Remote URL 1 `http://<mac-ip>:8766/phonebook.xml`, Display Name 1 `Claude`.

Both URLs are on the LAN only (daemon refuses anything outside `lan_cidr`).

## Ringtones

Each repo gets its own ringtone (1-8), sent as `Alert-Info: <http://127.0.0.1>;info=RingN`. The T31P plays it with no setup.
`phone list` shows each repo's ring. To pin one: `[repo_ringtones]` in `~/.claude-phone/config.toml`, e.g. `lemon-stand = 3`.

## 11. Screen messages (optional, for `screen_push`)

1. On the Mac, open **https://\<phone-ip\>** in a browser. It warns that the connection is not private
   (the phone's own certificate): click **Advanced**, then **Proceed**.
2. Log in as `admin` with the password you set in step 3.
3. **Features** tab → **Remote Control** (left menu).
4. **Push XML Server IP Address**: `<mac-ip>`. Confirm.

`phone meeting-test` checks it: the phone whispers and then shows a test meeting.

## 12. DND key (optional, with `meeting_alerts`)

So the phone's DND key and the meeting hold stay in step: the Mac switches DND on at each meeting alert and
off when the meeting ends; switching DND off yourself ends the hold early (meeting finished early); switching
it on outside a meeting holds Claude's calls too.

1. **Features → Remote Control → Action URI Allow IP List**: `<mac-ip>`. Confirm.
2. **Features → Action URL**: Open DND `http://<mac-ip>:8766/dnd/on`, Close DND `http://<mac-ip>:8766/dnd/off`. Confirm.
3. On the Mac, store the phone's admin password: `security add-generic-password -U -s claude-phone -a desk-admin -w`,
   then type the password at the prompt (not the command). Without it, only step 2 works: the DND key still
   controls the hold, but the phone won't show DND during meetings.

After a wrong password the phone locks its web login for a few minutes; the daemon stops trying after
one refusal until it restarts.
