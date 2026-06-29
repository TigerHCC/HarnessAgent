# SRUM schema — confirmed on this machine (2026-06-29 spike)

Source: `spike_srum.py` + decode probe against a VSS copy of `C:\Windows\System32\sru\SRUDB.dat`
(94 MB). `dissect.esedb` 3.18. 16 tables total.

## Library API (dissect.esedb 3.18) — confirmed
- `db = EseDB(open(path,'rb'))`, `db.tables()` → tables, `t.name`, `t.columns` (each `.name`), `t.records()`.
- Cell access: **`record.get("ColumnName")`** works (returns `None` when absent).

## Timestamp decoding — IMPORTANT
The `TimeStamp` column is returned by dissect as an **int64** (e.g. `4676574530268800250`).
It is an **OLE automation date stored as float8** — reinterpret the int64 *bits* as a double,
then it's **days since 1899-12-30 (UTC)**:
```python
import struct, datetime as dt
f = struct.unpack('<d', struct.pack('<q', int(ts)))[0]
when = dt.datetime(1899, 12, 30) + dt.timedelta(days=f)   # UTC
```
Verified: app row → 2023-06-05T09:05:00, network row → 2026-04-30T01:29:00.
(Do NOT use `float(ts)` directly — that treats 4.6e18 as days.)

## App identity
- `SruDbIdMapTable` (cols `IdType, IdIndex, IdBlob`) — **80,855 entries**. `AppId` in provider
  tables → `IdIndex`. Decode `IdBlob` as **UTF-16-LE**, strip NULs.
- App-type blobs look like `!!svchost.exe!2013/03/29:14:54:16!1f834![netsvcs] [WpnService]`,
  service short names (`Amsp`, `Dnscache`, `System`), or `\Device\HarddiskVolumeN\...\app.exe`.
  Some blobs are user SIDs (referenced by `UserId`, not `AppId`) and decode to garbage as UTF-16 —
  fine, since tools resolve `AppId`. Friendly-name helper: if `!!name!...!` take `name`; if a
  `\Device\...`/path, take the basename; else use as-is.

## Provider tables used by the MCP
- **App Resource Usage** `{D10CA2FE-6FCF-4F6D-848E-B2E99266FA89}`
  cols: `AppId, UserId, TimeStamp, ForegroundCycleTime, BackgroundCycleTime,
  ForegroundBytesRead, ForegroundBytesWritten, BackgroundBytesRead, BackgroundBytesWritten, …`
  → bytes_read = FG+BG BytesRead; bytes_written = FG+BG BytesWritten; cpu = FG+BG CycleTime (cycles).
- **Network Data Usage** `{973F5D5C-1D90-4944-BE8E-24B94231A174}`
  cols: `AppId, UserId, TimeStamp, InterfaceLuid, BytesSent, BytesRecvd, …` (real values present).
- **Energy Usage (long-term)** `{FEE4E14F-02A9-4550-B5CE-5FA2DA202E37}LT`
  cols: `AppId, UserId, TimeStamp, ActiveEnergy, CsEnergy, ActiveAcTime, ActiveDcTime,
  ActiveDischargeTime, DesignedCapacity, FullChargedCapacity, CycleCount, …`
  → per-app energy = ActiveEnergy + CsEnergy. NOTE: may be 0 on this hardware (energy estimation
  not always populated, esp. desktops) — report honestly.
- (Not used) `{FEE4E14F-…}` short-term = **battery state** history (ChargeLevel, FullChargedCapacity,
  CycleCount) — not per-app energy. `{D10CA2FE-…FA86}` = push notifications.

## Copy method — confirmed
`esentutl.exe /y <SRUDB.dat> /vss /d <dst>` succeeds while the DB is locked (needs admin).
