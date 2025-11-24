#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synchronise a school timetable and produce an iCalendar file for subscription.

This script is designed to be run from a GitHub Action or any other cron-like
environment. It logs into a school portal using credentials supplied via
environment variables, retrieves the user's events from a JSON endpoint and
exports them as a `.ics` file into the `docs/` directory. When used with
GitHub Pages, the generated `docs/edt.ics` will be served at
`https://<user>.github.io/<repo>/edt.ics`.

You must supply the following environment variables:

  - USERNAME / PASSWORD: your login credentials for the school portal. In a
    GitHub Action, these should be stored as repository secrets (for example
    ENT_USERNAME and ENT_PASSWORD) and mapped via the workflow file.
  - LOGIN_URL: the URL for the login form of your school's ENT.
  - EVENTS_URL: the URL returning your timetable in JSON form. This script
    assumes that the JSON response contains a top-level key "events" with a
    list of objects. Each object should contain at least `id`, `title`,
    `start`, `end` and optionally `room` and `description` fields. You may
    need to adapt the `fetch_events` function if your API differs.

You can customise the frequency of synchronisation by adjusting the cron
expression in your GitHub Action. By default, the example workflow runs every
10 hours.
"""

import os
import json
from datetime import datetime
from typing import List, Dict

import pytz
import requests
from icalendar import Calendar, Event


# Timezone for all events. Modify if needed.
TIMEZONE = pytz.timezone("Europe/Paris")

# Read environment variables. These must be provided in the runner environment.
USERNAME = os.getenv("USERNAME")
PASSWORD = os.getenv("PASSWORD")
LOGIN_URL = os.getenv("LOGIN_URL", "")
EVENTS_URL = os.getenv("EVENTS_URL", "")


def login(session: requests.Session) -> None:
    """Authenticate to the portal. Raises an exception if login fails."""
    if not USERNAME or not PASSWORD:
        raise ValueError(
            "USERNAME and PASSWORD environment variables must be set."
        )
    if not LOGIN_URL:
        raise ValueError("LOGIN_URL environment variable must be set.")
    payload = {"username": USERNAME, "password": PASSWORD}
    resp = session.post(LOGIN_URL, data=payload)
    resp.raise_for_status()


def fetch_events(session: requests.Session) -> List[Dict]:
    """Fetch events from the portal and return a list of event dicts.

    The default implementation assumes the endpoint returns JSON with a
    top-level key "events". Each event should include ISO‑formatted
    datetimes for `start` and `end`. Adapt this function as necessary.
    """
    if not EVENTS_URL:
        raise ValueError("EVENTS_URL environment variable must be set.")
    resp = session.get(EVENTS_URL)
    resp.raise_for_status()
    data = resp.json()
    events_data = data.get("events", [])
    events = []
    for item in events_data:
        try:
            start_dt = (
                datetime.fromisoformat(item["start"]).astimezone(TIMEZONE)
            )
            end_dt = (
                datetime.fromisoformat(item["end"]).astimezone(TIMEZONE)
            )
        except Exception as exc:
            # Skip events with invalid date formats
            print(f"Skipping event due to date parse error: {exc}")
            continue
        events.append(
            {
                "uid": str(item.get("id", "")),
                "summary": item.get("title", "Cours"),
                "start": start_dt,
                "end": end_dt,
                "location": item.get("room", ""),
                "description": item.get("description", ""),
            }
        )
    return events


def build_calendar(events: List[Dict]) -> Calendar:
    """Build an iCalendar object from a list of event dictionaries."""
    cal = Calendar()
    cal.add("prodid", "-//EDT Sync//github.com//")
    cal.add("version", "2.0")
    for e in events:
        vevent = Event()
        # Use the UID if provided; fallback to summary and time
        uid = e["uid"] or f"{e['summary']}-{int(e['start'].timestamp())}"
        vevent.add("uid", uid)
        vevent.add("summary", e["summary"])
        vevent.add("dtstart", e["start"])
        vevent.add("dtend", e["end"])
        if e.get("location"):
            vevent.add("location", e["location"])
        if e.get("description"):
            vevent.add("description", e["description"])
        cal.add_component(vevent)
    return cal


def write_calendar(calendar: Calendar, path: str) -> None:
    """Write the iCalendar object to the specified path."""
    with open(path, "wb") as f:
        f.write(calendar.to_ical())


def main() -> None:
    session = requests.Session()
    # Attempt login and fetch events
    login(session)
    events = fetch_events(session)
    print(f"Fetched {len(events)} events.")
    calendar = build_calendar(events)
    # Ensure docs directory exists
    os.makedirs("docs", exist_ok=True)
    write_calendar(calendar, os.path.join("docs", "edt.ics"))
    print("Calendar written to docs/edt.ics")


if __name__ == "__main__":
    main()