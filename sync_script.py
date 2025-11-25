#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synchronise a school timetable and produce an iCalendar file for subscription.

This script is designed to be run from a GitHub Action or any other cron-like
environment. It logs into a school portal using credentials supplied via
environment variables, retrieves the user's events by mimicking the browser's
AJAX call and exports them as a `.ics` file into the `docs/` directory.

You must supply the following environment variables:

  - USERNAME / PASSWORD: your login credentials for the school portal. In a
    GitHub Action, these should be stored as repository secrets (for example
    ENT_USERNAME and ENT_PASSWORD) and mapped via the workflow file.
  - LOGIN_URL: the URL for the login form of your school's ENT.
  - PLANNING_URL: the URL of the planning page (e.g. `faces/Planning.xhtml`).
    The script uses this page to extract the dynamic `ViewState` token and
    to send an AJAX POST that returns event data. You may need to adapt the
    `fetch_events` function if your ENT uses different parameters.

You can customise the frequency of synchronisation by adjusting the cron
expression in your GitHub Action. By default, the example workflow runs every
10 hours.
"""

import os
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict

import pytz
import requests
from icalendar import Calendar, Event
import urllib3
from urllib3.exceptions import InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)

# Timezone for all events. Modify if needed.
TIMEZONE = pytz.timezone("Europe/Paris")

# Read environment variables. These must be provided in the runner environment.
USERNAME = os.getenv("USERNAME")
PASSWORD = os.getenv("PASSWORD")
LOGIN_URL = os.getenv("LOGIN_URL", "")
# PLANNING_URL points to the Planning.xhtml page. It is used to both
# retrieve the ViewState token and perform the AJAX POST to fetch events.
PLANNING_URL = os.getenv("ENT_EVENTS_URL", "")

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

def _extract_viewstate(html: str) -> str:
    """Extract the javax.faces.ViewState value from the planning HTML page."""
    match = re.search(r'name="javax\.faces\.ViewState" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("Could not find ViewState token on the planning page")
    return match.group(1)

def fetch_events(session: requests.Session) -> List[Dict]:
    """
    Fetch events by performing the same AJAX POST as observed in the browser's Network tab.

    Steps:
    1. Request the planning page to obtain the current ViewState token.
    2. Build the POST payload using the ViewState and the desired date range (current week).
    3. Send the POST with appropriate headers and parse the JSON embedded in the XML response.
    4. Return a list of event dicts with parsed datetime objects.
    """
    if not PLANNING_URL:
        raise ValueError("PLANNING_URL environment variable must be set.")

    # Step 1: retrieve the planning page to get the ViewState token
    resp = session.get(PLANNING_URL)
    resp.raise_for_status()
    viewstate = _extract_viewstate(resp.text)

    # Compute date range for the current week
    now = datetime.now()
    start_of_week = now - timedelta(days=now.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    start_ts = int(start_of_week.timestamp() * 1000)
    end_ts = int(end_of_week.timestamp() * 1000)

    # Build the form payload based on the captured cURL. Some fields are constant,
    # but start and end timestamps are dynamic to reflect the chosen date range.
    payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": "form:j_idt117",
        "javax.faces.partial.execute": "form:j_idt117",
        "javax.faces.partial.render": "form:j_idt117",
        "form:j_idt117": "form:j_idt117",
        "form:j_idt117_start": start_ts,
        "form:j_idt117_end": end_ts,
        "form": "form",
        "form:largeurDivCenter": "",
        # idInit identifies the planning component; use the value captured from your cURL.
        "form:idInit": "webscolaapp.Planning_9156244072397193466",
        "form:date_input": now.strftime("%d/%m/%Y"),
        "form:week": f"{now.isocalendar().week}-{now.year}",
        "form:j_idt117_view": "agendaWeek",
        "form:offsetFuseauNavigateur": "-3600000",
        "form:onglets_activeIndex": "0",
        "form:onglets_scrollState": "0",
        "javax.faces.ViewState": viewstate,
    }

    headers = {
        "Faces-Request": "partial/ajax",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/xml, text/xml, */*; q=0.01",
    }

    # Step 2: perform the AJAX POST to retrieve the planning data
    post_resp = session.post(PLANNING_URL, data=payload, headers=headers)
    post_resp.raise_for_status()

    # Step 3: extract the JSON from the response. The server returns a partial JSF
    # update that may contain JSON inside. Look for the first JSON object using regex.
    match = re.search(r'\{.*\}', post_resp.text, re.DOTALL)
    if not match:
        print("No JSON object found in the response. Response content:\n", post_resp.text[:500])
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        print("Failed to decode JSON from response:", e)
        return []

    events_data = data.get("events", [])
    events: List[Dict] = []
    for item in events_data:
        try:
            start_dt = datetime.fromisoformat(item["start"]).astimezone(TIMEZONE)
            end_dt = datetime.fromisoformat(item["end"]).astimezone(TIMEZONE)
        except Exception as exc:
            print(f"Skipping event due to date parse error: {exc}")
            continue
        events.append({
            "uid": str(item.get("id", "")),
            "summary": item.get("title", "Cours"),
            "start": start_dt,
            "end": end_dt,
            "location": item.get("room", ""),
            "description": item.get("description", ""),
        })
    return events

def build_calendar(events: List[Dict]) -> Calendar:
    """Build an iCalendar object from a list of event dictionaries."""
    cal = Calendar()
    cal.add("prodid", "-//EDT Sync//github.com//")
    cal.add("version", "2.0")
    for e in events:
        vevent = Event()
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
    # Ne pas vérifier les certificats SSL, car le certificat de l'ENT n'est pas reconnu
    session.verify = False
    login(session)
    events = fetch_events(session)
    print(f"Fetched {len(events)} events.")
    calendar = build_calendar(events)
    os.makedirs("docs", exist_ok=True)
    write_calendar(calendar, os.path.join("docs", "edt.ics"))
    print("Calendar written to docs/edt.ics")

if __name__ == "__main__":
    main()
