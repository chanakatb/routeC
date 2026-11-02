#!/usr/bin/env python3
"""
Builds schedule.json for the web page from Unitrans' official GTFS feed.
Run by the "Update schedule" GitHub Action once a day.

  python build_schedule.py              # download from Unitrans
  python build_schedule.py feed.zip     # use a local GTFS zip
"""
import json
import sys

from c_line_alert import GTFS_URL, ROUTE, STOPS, load_gtfs, route_ids

g = load_gtfs(sys.argv[1] if len(sys.argv) > 1 else GTFS_URL)
rids = route_ids(g)
if not rids:
    sys.exit(f"Route {ROUTE} not found in the Unitrans feed.")

trip_service = {t["trip_id"]: t["service_id"] for t in g["trips"] if t["route_id"] in rids}
stop_name = {s["stop_id"]: s["stop_name"].lower() for s in g["stops"]}

# Skip each trip's final stop (that's an arrival, not a departure).
last_seq = {}
for st in g["stop_times"]:
    if st["trip_id"] in trip_service:
        seq = int(st["stop_sequence"])
        last_seq[st["trip_id"]] = max(seq, last_seq.get(st["trip_id"], -1))

stops = {label: set() for label in STOPS}
for st in g["stop_times"]:
    tid = st["trip_id"]
    if tid not in trip_service or int(st["stop_sequence"]) == last_seq[tid]:
        continue
    t = (st.get("departure_time") or st.get("arrival_time") or "").strip()
    if not t:
        continue
    name = stop_name.get(st["stop_id"], "")
    for label, needle in STOPS.items():
        if needle.lower() in name:
            stops[label].add((trip_service[tid], ":".join(t.split(":")[:2]), tid))

for label, times in stops.items():
    if not times:
        sys.exit(f"No C line departures found for '{label}'. Check STOP_1/STOP_2.")

used = set(trip_service.values())

# ---- Extra data for live tracking and the map ----
c_trips = {t["trip_id"]: t for t in g["trips"] if t["route_id"] in rids}
c_stop_ids = {st["stop_id"] for st in g["stop_times"] if st["trip_id"] in c_trips}
stop_rows = {s["stop_id"]: s for s in g["stops"]}
targets = {label: sorted(sid for sid in c_stop_ids
                         if needle.lower() in stop_name.get(sid, ""))
           for label, needle in STOPS.items()}

# Which direction leaves each stop (Silo -> outbound, Wake Forest/Cuarto -> inbound).
from collections import Counter
dir_votes = {label: Counter() for label in STOPS}
for st in g["stop_times"]:
    tid = st["trip_id"]
    if tid in c_trips and int(st["stop_sequence"]) != last_seq[tid]:
        for label, ids in targets.items():
            if st["stop_id"] in ids:
                dir_votes[label][c_trips[tid].get("direction_id", "")] += 1
label_dir = {label: (v.most_common(1)[0][0] if v else "") for label, v in dir_votes.items()}
headsigns = {}
for t in c_trips.values():
    headsigns.setdefault(t.get("direction_id", ""), Counter())[t.get("trip_headsign", "")] += 1

shape_ids = {t.get("shape_id") for t in c_trips.values() if t.get("shape_id")}
shapes = {}
for row in g.get("shapes", []):
    if row["shape_id"] in shape_ids:
        shapes.setdefault(row["shape_id"], []).append(
            (int(row["shape_pt_sequence"]), round(float(row["shape_pt_lat"]), 5),
             round(float(row["shape_pt_lon"]), 5)))
shapes = {k: [[la, lo] for _, la, lo in sorted(v)] for k, v in shapes.items()}

live = {
    "route_ids": sorted(rids),
    "targets": targets,
    "label_dir": label_dir,
    "headsigns": {d: c.most_common(1)[0][0] for d, c in headsigns.items()},
    "trips": {tid: [t.get("direction_id", ""), last_seq[tid]] for tid, t in c_trips.items()},
    "stops": {sid: [stop_rows[sid]["stop_name"], round(float(stop_rows[sid]["stop_lat"]), 6),
                    round(float(stop_rows[sid]["stop_lon"]), 6)]
              for sid in c_stop_ids if stop_rows.get(sid, {}).get("stop_lat")},
    "shapes": shapes,
}
days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
out = {
    "route": ROUTE,
    "stops": {k: sorted(v, key=lambda x: (x[0], x[1].zfill(5))) for k, v in stops.items()},
    "calendar": [
        {k: c[k] for k in ["service_id", "start_date", "end_date", *days]}
        for c in g["calendar"] if c["service_id"] in used
    ],
    "live": live,
    "calendar_dates": [
        {k: c[k] for k in ["service_id", "date", "exception_type"]}
        for c in g["calendar_dates"] if c["service_id"] in used
    ],
}
with open("schedule.json", "w") as f:
    json.dump(out, f, indent=1, sort_keys=True)
print({k: len(v) for k, v in out["stops"].items()}, "departures written to schedule.json")
