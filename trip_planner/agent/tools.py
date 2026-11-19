import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
from langchain.tools import tool
from langchain_tavily import TavilySearch
load_dotenv()
# Use these URLs for their corresponding tools
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
ATTRACTIONS_URL = "https://app.ticketmaster.com/discovery/v2/attractions.json"

FORECAST_DAYS = 16          # all Open-Meteo will forecast: today + 15
MAX_RESULTS = 3

# Open-Meteo returns a WMO code, not words. Only the common ones are listed;
# anything else falls through to "code N", which will keep the agent honest.
WEATHER_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}



def _search(query: str) -> str:
    """Run one web search and format the hits. Shared by the four find_* tools."""
    try:
        results = TavilySearch(max_results=MAX_RESULTS).invoke({"query": query})
    except Exception as e:
        return f"The search failed ({e}). Tell the user you could not look this up."

    hits = results.get("results", []) if isinstance(results, dict) else []
    if not hits:
        return (f"No results for '{query}'. Try a nearby larger city, or tell the "
                f"user you could not find anything — do not fill the gap from memory.")

    lines = []
    for i, h in enumerate(hits, 1):
        snippet = " ".join((h.get("content") or "").split())[:300]
        lines.append(f"{i}. {h.get('title', 'untitled')}\n   {h.get('url', '')}\n   {snippet}")
    return "\n".join(lines) + (
        "\nThese snippets are the only evidence you have. They do not include "
        "opening hours, and a price counts only if it is written above.")


def _geocode(city: str) -> tuple[str, float, float, str]:
    """City name -> (label, latitude, longitude, ambiguity_note). Written for you.

    Raises ValueError if nothing matches. The ambiguity note is the interesting
    part: this API ranks by population, so "Cambridge" puts England ahead of
    Massachusetts and "Portland" puts Oregon ahead of Maine.
    """
    r = requests.get(GEOCODE_URL, params={"name": city.strip(), "count": 5,
                                          "language": "en", "format": "json"},
                     timeout=15)
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    if not results:
        raise ValueError(f"No place matches '{city}'.")

    def describe(r):
        return ", ".join(x for x in (r["name"], r.get("admin1"), r.get("country")) if x)

    top = results[0]
    label = describe(top)

    # Every US city shares its name with a hamlet somewhere; "Boston, Georgia (pop. 1,400)"
    # is noise, and an agent that hedges on every city stops being useful.
    top_pop = top.get("population") or 0
    rivals = [describe(o) for o in results[1:4]
              if o["name"].lower() == top["name"].lower()
              and (o.get("population") or 0) >= 0.10 * top_pop]
    note = (f" NOTE: '{city}' is ambiguous — could also be {'; '.join(rivals)}. "
            f"Confirm which one before planning." if rivals else "")
    return label, top["latitude"], top["longitude"], note


GEOCODE_ERRORS = (ValueError, requests.RequestException)


def _geocode_failure(city: str, error: Exception) -> str:
    """Turn a _geocode exception into a sentence the model can act on."""
    if isinstance(error, requests.RequestException):
        return (f"Could not look up where '{city}' is: the geocoding service failed "
                f"({error}). Tell the user you could not check, rather than guessing.")
    return (f"No place matches '{city}'. Ask the user to check the spelling or name "
            f"a nearby larger city. Do not guess where they meant.")



@tool
def get_weather(city: str, date: str = "") -> str:
    """Get the weather forecast for a city on a date (YYYY-MM-DD).

    Only the next 16 days can be forecast (mention the window by start and end date relatively). 
    For any date beyond that the forecast does not exist, and this tool will say so. 
    Say that plainly instead of describing what the season is usually like. Leave date empty for today.

    The city name is resolved to a real place, and this tool tells you WHICH
    place it picked. If other places share the name, it says so: check it matches
    what the user meant before you plan anything.
    """

    try:
        label, latitude, longitude, note = _geocode(city)
    except GEOCODE_ERRORS as e:
        return _geocode_failure(city, e)

    params = {
    "latitude": latitude,
    "longitude": longitude,
    "daily": "weather_code",
    "timezone": "auto",
    "forecast_days": 16,
    }

    try:
        response = requests.get(url=FORECAST_URL, params=params, timeout=15)
    except requests.RequestException as e:
        return f"The forecast service failed ({e}). Tell the user you could not check the weather."

    if response.status_code==200:

        daily = response.json()["daily"]
        first = daily["time"][0]
        last = daily["time"][-1]
        if not date:
            date = first
        if date not in daily["time"]:
            return f"No forecast for {date} only {first} to {last} available"

        i = daily["time"].index(date)

        weather_code = daily["weather_code"][i]
        condition = WEATHER_CODES.get(weather_code, f"code {weather_code}")

        return f"Forecast for {label} on {date}: {condition}. {note}"

    return f"Error: {response.status_code}"

@tool
def find_attractions(city: str, interest: str = "") -> str:
    """Find sights, museums, parks and landmarks in a city.

    interest narrows it, e.g. "art", "history", "outdoors", "free".
    Use this for things to SEE. For food use find_restaurants; for things
    happening on a particular date use find_events.
    """
    try:
        label, _, _, note = _geocode(city)
    except GEOCODE_ERRORS as e:
        return _geocode_failure(city, e)

    query = f"Find attractions in {label} and the attraction of interest is {interest}."

    return note + _search(query)


@tool
def find_restaurants(city: str, budget: str = "") -> str:
    """Find places to eat in a city.

    budget narrows it, e.g. "cheap", "mid-range", "fine dining".
    Use this for food and drink only. It does not return opening hours or
    current prices, so do not promise either.
    """


    try:
        label, _, _, note = _geocode(city)
    except GEOCODE_ERRORS as e:
        return _geocode_failure(city, e)

    query = f"Find places to eat in {label} for a {budget} budget"
    return note + _search(query)


def _price_text(event: dict) -> str:
    """Ticketmaster rarely publishes a price, and a 0-0 range is a placeholder."""
    ranges = event.get("priceRanges")
    if not ranges:
        return "price not published"
    lo, hi = ranges[0].get("min", 0), ranges[0].get("max", 0)
    if lo == 0 and hi == 0:
        return "price listed as 0 (placeholder, not free)"
    return f"{lo}–{hi} {ranges[0].get('currency', '')}".strip()


def _format_event(event: dict) -> str:
    """One Ticketmaster event as a line: name | local date | venue, city | price | url."""
    venues = event.get("_embedded", {}).get("venues") or [{}]
    venue = venues[0].get("name", "venue TBA")
    venue_city = venues[0].get("city", {}).get("name")
    where = f"{venue}, {venue_city}" if venue_city else venue
    when = event.get("dates", {}).get("start", {}).get("localDate", "date TBA")
    return (f"- {event.get('name', 'untitled')} | {when} | {where} | "
            f"{_price_text(event)} | {event.get('url', 'no link')}")


@tool
def find_events(city: str, date: str = "", category: str = "") -> str:
    """Find real ticketed events in a city: concerts, sport, theatre, festivals.

    date is YYYY-MM-DD and means that day in the venue's own local time; leave
    it empty for the week ahead. Unlike the search tools this returns structured
    records: real dates, real venues, real links.

    category narrows by type. The five broad ones always work: "music", "sports",
    "arts & theatre", "film", "miscellaneous". Many narrower ones work too —
    "comedy", "rock", "jazz", "classical", "hip-hop", "country", "pop",
    "basketball", "hockey", "theatre", "musical", "dance", "family". Leave it
    empty for everything.

    But some plausible-sounding words match nothing at all ("opera", "magic"),
    and a category the API does not recognise returns ZERO EVENTS rather than an
    error — identical to a genuinely quiet weekend. Prefer a word from the list
    above, and never report "there is nothing on" from an empty filtered result
    without checking again without the filter.

    MOST EVENTS COME BACK WITH NO PRICE. The API documents a price field and
    fills it in for only a small fraction of events, under 10% in every city
    tested. "price not published" means exactly that. Do not substitute a guess,
    and do not read a missing price as free admission. A price published as 0 is
    a placeholder, not a free ticket.

    This only covers events sold through Ticketmaster. A quiet result does not
    mean nothing is happening. Say that rather than implying the city is empty.
    """
   

    try:
        label, _, _, note = _geocode(city)
    except GEOCODE_ERRORS as e:
        return _geocode_failure(city, e)


    today = datetime.now()
    if date:
        local_start = f"{date}T00:00:00"
        local_end = f"{date}T23:59:59"
    else:
        today = datetime.now()
        local_start = today.strftime("%Y-%m-%dT00:00:00")
        local_end = (today + timedelta(days=7)).strftime("%Y-%m-%dT23:59:59")

    params = {
        "apikey": os.getenv("TICKETMASTER_API_KEY"),
        "city": city,
        # localEndDateTime is not a real parameter and is silently ignored, which
        # leaves the window open-ended. The combined "start,end" form closes it.
        "localStartEndDateTime": f"{local_start},{local_end}",
        "size": 20,
    }
    if category:
        params["classificationName"] = category

    try:
        response = requests.get(url=EVENTS_URL, params=params, timeout=20)
    except requests.RequestException as e:
        return f"The Ticketmaster search failed ({e}). Tell the user you could not check events."


    if response.status_code==200:

        result = response.json().get("_embedded", {}).get("events",[])
        if not result:
            return "No events found"
        
        else:
            message = "\n".join(_format_event(event) for event in result)

        return f"Events in {label} are as follows: {message}. {note}"

    return f"Error: {response.status_code}"


@tool
def get_transit_info(city: str) -> str:
    """Get general information about getting around a city: metro, bus, passes, fares.

    This returns written guidance, NOT timetables and NOT journey times. If the
    user asks how many minutes a specific trip takes, say you cannot look that
    up rather than estimating it.
    """

    try:
        label, _, _, note = _geocode(city)
    except GEOCODE_ERRORS as e:
        return _geocode_failure(city, e)

    query = f"How do you get around in the city of {city}. Exact location is {label}"

    return f"Transit information for {label}: {_search(query)}. {note}"



@tool
def find_performer_events(performer: str, city: str, date: str = "") -> str:
    """Find when and where a specific performer, team or touring show plays in a city.

    Use this when the user names an act ("Are the Celtics playing while I'm in
    Boston?", "Is Hamilton on in Chicago?"). For "what's on in a city" with no act
    named, use find_events instead.

    date is YYYY-MM-DD (that day in the venue's local time). Leave it empty to see
    all upcoming dates in the city.

    An "attraction" in Ticketmaster means a performer, team or touring show, NOT a
    tourist sight. This tool cannot replace find_attractions: searching it for
    "museum" returns touring ticketed exhibitions, not the city's museums.

    This tool can return four different answers. Relay them as they are:
      - no performer matches the name: say so, do not guess another act;
      - several acts match the name: list them and ask the user which one they
        meant before going on. Never pick one yourself;
      - the performer exists but has nothing in that city or on that date: that
        is a real answer, and it is different from "no such performer";
      - the events themselves, with real dates and venues. Prices follow the same
        rules as find_events: "price not published" is not free.
    """
    key = os.getenv("TICKETMASTER_API_KEY")
    if not key:
        return "Ticketmaster is not configured (no API key), so performer events are unavailable."

    # Step 1: name -> attraction id.
    try:
        r = requests.get(ATTRACTIONS_URL, timeout=20,
                         params={"apikey": key, "keyword": performer, "size": 20})
        r.raise_for_status()
        attractions = r.json().get("_embedded", {}).get("attractions", [])
    except (requests.RequestException, ValueError) as e:
        return f"The Ticketmaster performer search failed ({e}). Tell the user you could not check."

    if not attractions:
        return (f"No performer, team or show called '{performer}' is listed on "
                f"Ticketmaster. Do not guess a similar act.")

    def describe(a):
        upcoming = a.get("upcomingEvents", {}).get("_total", 0)
        return f"{a['name']} ({upcoming} upcoming events)"

    # The keyword search is fuzzy and ranks by popularity, so the first hit is often
    # not the act asked for: "Taylor Swift" returns tribute bands ahead of her.
    exact = [a for a in attractions if a["name"].strip().lower() == performer.strip().lower()]
    if len(exact) == 1:
        match = exact[0]
    elif len(exact) == 0 and len(attractions) == 1:
        match = attractions[0]
    else:
        options = exact or attractions[:8]
        listed = "\n".join(f"- {describe(a)}" for a in options)
        return (f"'{performer}' is ambiguous: it matches several acts on Ticketmaster.\n"
                f"{listed}\nAsk the user which one they mean. Do not pick one yourself.")

    name = match["name"]
    picked = "" if name.lower() == performer.strip().lower() else f" (closest match to '{performer}')"
    if match.get("upcomingEvents", {}).get("_total", 0) == 0:
        return f"{name}{picked} is on Ticketmaster but has no upcoming events anywhere."

    # Step 2: attraction id + city (+ date) -> events.
    params = {"apikey": key, "attractionId": match["id"], "city": city,
              "sort": "date,asc", "size": 20}
    if date:
        params["localStartEndDateTime"] = f"{date}T00:00:00,{date}T23:59:59"
    try:
        r = requests.get(EVENTS_URL, params=params, timeout=20)
        r.raise_for_status()
        events = r.json().get("_embedded", {}).get("events", [])
    except (requests.RequestException, ValueError) as e:
        return f"Found {name}, but the event search failed ({e}). Tell the user you could not check."

    when = f"on {date}" if date else "in the upcoming schedule"
    if not events:
        return (f"{name}{picked} exists and has upcoming events, but none in {city} {when}. "
                f"This covers Ticketmaster listings only.")

    lines = "\n".join(_format_event(e) for e in events)
    return f"{name}{picked} in {city} {when}:\n{lines}"


TOOLS = [get_weather, get_transit_info, find_restaurants, find_events, find_attractions, find_performer_events]

    
