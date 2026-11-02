#!/usr/bin/env python3
"""
Unitrans C line -> text message alert.

Reads Unitrans' official GTFS schedule feed, finds the next C line departures
from "Silo / Main Island" and "Wake Forest / Cuarto", and texts them to you.

Usage:
  python c_line_alert.py --print          # just print, no text sent
  python c_line_alert.py --send           # send a text (see SEND METHODS below)
  python c_line_alert.py --list-stops     # show every C line stop name in the feed

SEND METHODS (pick one, set via environment variables):
  1) Twilio (most reliable):
       TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM (+1xxxxxxxxxx), TO_PHONE (+1xxxxxxxxxx)
  2) Free carrier email-to-SMS gateway via Gmail:
       GMAIL_USER, GMAIL_APP_PASSWORD, SMS_GATEWAY_ADDRESS
       e.g. 5305551234@vtext.com (Verizon), @tmomail.net (T-Mobile),
            @txt.att.net (AT&T)
"""

import argparse
import csv
import io
import os
import smtplib
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

GTFS_URL = os.environ.get(
    "GTFS_URL", "https://unitrans.ucdavis.edu/media/gtfs/Unitrans_GTFS.zip"
)
ROUTE = os.environ.get("ROUTE", "C")
# Substrings matched (case-insensitive) against GTFS stop names.
STOPS = {
    "Silo": os.environ.get("STOP_1", "silo"),
    "Wake Forest/Cuarto": os.environ.get("STOP_2", "cuarto"),
}
HOW_MANY = int(os.environ.get("HOW_MANY", "3"))
TZ = ZoneInfo("America/Los_Angeles")  # Davis, CA: auto-switches PST/PDT

# Terms exactly as shown in the Unitrans "Term" dropdown (start, end inclusive).
TERMS = [
    ("Weekday (Fall Quarter)",          "2026-10-23", "2026-12-04", "weekday"),
    ("Weekend/Holiday (Fall Quarter)",  "2026-10-23", "2026-12-06", "weekend"),
    ("Finals Service",                  "2026-12-07", "2026-12-11", "any"),
    ("Weekday (Winter Break)",          "2026-12-14", "2027-01-01", "weekday"),
    ("Weekend/Holiday (Winter Break)",  "2026-12-12", "2027-01-03", "weekend"),
]


def pick_term(day):
    """Choose the Term + Days the Unitrans site would show for this date."""
    iso = day.isoformat()
    is_weekend = day.weekday() >= 5
    for name, start, end, kind in TERMS:
        if start <= iso <= end and (
            kind == "any" or (kind == "weekend") == is_weekend
        ):
            term = name
            break
    else:
        term = "Term not in built-in list (using Unitrans feed)"
    if is_weekend:
        days = "Weekend (no C service)"
    elif day.weekday() == 2:
        days = "Wednesday"
    elif day.weekday() == 4:
        days = "Friday"
    else:
        days = "Mon, Tue, Thu"
    return term, days


# ---------- GTFS loading ----------
def load_gtfs(url_or_path):
    if os.path.exists(url_or_path):
        data = open(url_or_path, "rb").read()
    else:
        req = urllib.request.Request(url_or_path, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    z = zipfile.ZipFile(io.BytesIO(data))

    def table(name):
        if name not in z.namelist():
            return []
        text = z.read(name).decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    return {n: table(n + ".txt") for n in
            ["routes", "trips", "stop_times", "stops", "calendar", "calendar_dates", "shapes"]}


def active_services(g, day):
    """Service IDs running on a given date (calendar + calendar_dates exceptions)."""
    ymd = day.strftime("%Y%m%d")
    weekday = ["monday", "tuesday", "wednesday", "thursday",
               "friday", "saturday", "sunday"][day.weekday()]
    active = {
        c["service_id"] for c in g["calendar"]
        if c["start_date"] <= ymd <= c["end_date"] and c.get(weekday) == "1"
    }
    for cd in g["calendar_dates"]:
        if cd["date"] == ymd:
            if cd["exception_type"] == "1":
                active.add(cd["service_id"])
            elif cd["exception_type"] == "2":
                active.discard(cd["service_id"])
    return active


def route_ids(g):
    return {r["route_id"] for r in g["routes"]
            if r.get("route_short_name", "").strip().upper() == ROUTE.upper()
            or r["route_id"].strip().upper() == ROUTE.upper()}


def route_stop_names(g):
    rids = route_ids(g)
    trip_ids = {t["trip_id"] for t in g["trips"] if t["route_id"] in rids}
    stop_ids = {st["stop_id"] for st in g["stop_times"] if st["trip_id"] in trip_ids}
    return sorted({s["stop_name"] for s in g["stops"] if s["stop_id"] in stop_ids})


def to_dt(day, hhmmss):
    h, m, s = (int(x) for x in hhmmss.strip().split(":"))
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return base + timedelta(hours=h, minutes=m, seconds=s)  # handles 24:xx+


# ---------- Core logic ----------
def next_departures(g, now):
    rids = route_ids(g)
    if not rids:
        sys.exit(f"Route {ROUTE} not found in GTFS feed.")
    stop_lookup = {s["stop_id"]: s["stop_name"] for s in g["stops"]}

    # Last stop of each trip, so we don't list arrivals at the final stop as departures.
    last_seq = {}
    for st in g["stop_times"]:
        seq = int(st["stop_sequence"])
        if seq > last_seq.get(st["trip_id"], -1):
            last_seq[st["trip_id"]] = seq

    results = {label: [] for label in STOPS}
    # Check yesterday (for after-midnight trips), today, and the next 6 days.
    for offset in range(-1, 7):
        day = (now + timedelta(days=offset)).date()
        services = active_services(g, day)
        trips = {t["trip_id"] for t in g["trips"]
                 if t["route_id"] in rids and t["service_id"] in services}
        for st in g["stop_times"]:
            if st["trip_id"] not in trips:
                continue
            if int(st["stop_sequence"]) == last_seq[st["trip_id"]]:
                continue
            name = stop_lookup.get(st["stop_id"], "").lower()
            t = st.get("departure_time") or st.get("arrival_time")
            if not t:
                continue
            for label, needle in STOPS.items():
                if needle.lower() in name:
                    dt = to_dt(day, t)
                    if dt >= now:
                        results[label].append(dt)
    for label in results:
        results[label] = sorted(set(results[label]))[:HOW_MANY]
    return results


def format_message(results, now, ran_today=True):
    term, days = pick_term(now.date())
    lines = [f"Unitrans {ROUTE} line ({now:%a %b %-d, %-I:%M %p %Z})",
             f"{term} | {days}"]
    if not ran_today and now.weekday() < 5:
        lines.append("No C service today (holiday/exception)")
    for label, times in results.items():
        if not times:
            lines.append(f"{label}: no upcoming buses found")
            continue
        parts = []
        for dt in times:
            mins = int((dt - now).total_seconds() // 60)
            when = f"{dt:%-I:%M %p}"
            if dt.date() != now.date():
                when = f"{dt:%a} {when}"
            parts.append(f"{when} ({mins}m)" if mins < 120 else when)
        lines.append(f"{label}: " + ", ".join(parts))
    return "\n".join(lines)


# ---------- Sending ----------
def send_text(body):
    if os.environ.get("TWILIO_SID"):
        import base64
        import urllib.parse
        sid, token = os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"]
        data = urllib.parse.urlencode({
            "From": os.environ["TWILIO_FROM"],
            "To": os.environ["TO_PHONE"],
            "Body": body,
        }).encode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=data)
        auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        urllib.request.urlopen(req, timeout=30)
        print("Sent via Twilio.")
    elif os.environ.get("GMAIL_USER"):
        msg = MIMEText(body)
        msg["From"] = os.environ["GMAIL_USER"]
        msg["To"] = os.environ["SMS_GATEWAY_ADDRESS"]
        msg["Subject"] = ""
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
            s.send_message(msg)
        print("Sent via email-to-SMS gateway.")
    else:
        sys.exit("No send method configured. Set Twilio or Gmail env vars.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print only")
    ap.add_argument("--send", action="store_true", help="send a text")
    ap.add_argument("--list-stops", action="store_true")
    ap.add_argument("--only-at-hours", default="",
                    help="e.g. 7,15 - only send if the Davis local hour matches")
    ap.add_argument("--gtfs", default=GTFS_URL, help="GTFS zip URL or local path")
    args = ap.parse_args()

    if args.only_at_hours and args.send:
        hours = {int(h) for h in args.only_at_hours.split(",")}
        if datetime.now(TZ).hour not in hours:
            print("Not the scheduled Davis hour (DST duplicate run); skipping.")
            return

    g = load_gtfs(args.gtfs)
    if args.list_stops:
        print("\n".join(route_stop_names(g)))
        return

    now = datetime.now(TZ)
    today_services = active_services(g, now.date())
    ran_today = any(t["service_id"] in today_services
                    for t in g["trips"] if t["route_id"] in route_ids(g))
    msg = format_message(next_departures(g, now), now, ran_today)
    print(msg)
    if args.send:
        send_text(msg)


if __name__ == "__main__":
    main()
