"""Trip planner: Tools

Two rules, the same ones from the Husky Tech agent:
  1. Return a readable string. Never raise, never return a bare dict.
  2. When you have nothing useful, SAY SO in the return value. "No results —
     try a nearby larger city" is a sentence the model can act on.
"""

import os
import json
import pygeohash as pgh
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
import requests
from langchain.tools import tool
from langchain_tavily import TavilySearch
load_dotenv()
# Use these URLs for their corresponding tools
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
ATTRACTIONS_URL="/discovery/v2/attractions"

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


# NOTE: The tools already have some docstring descriptions
# but you can modify them if you find the descriptions insufficient or inaccurate.
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

    label, latitude, longitude, note = _geocode(f"{city}")
    

    params = {
    "latitude": latitude,
    "longitude": longitude,
    "daily": "weather_code",
    "timezone": "auto",
    "forecast_days": 16,
    }   

    response = requests.get(url=FORECAST_URL, params=params)

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
        condition = WEATHER_CODES[weather_code]

        return f"Forecast for {label} on {date}: {condition}. {note}"

    return f"Error: {response.status_code}"

@tool
def find_attractions(city: str, interest: str = "") -> str:
    """Find sights, museums, parks and landmarks in a city.

    interest narrows it, e.g. "art", "history", "outdoors", "free".
    Use this for things to SEE. For food use find_restaurants; for things
    happening on a particular date use find_events.
    """
    label, _, _, note= _geocode(city)


    query = f"Find attractions in {label} and the attraction of interest is {interest}."

    return note + _search(query)


@tool
def find_restaurants(city: str, budget: str = "") -> str:
    """Find places to eat in a city.

    budget narrows it, e.g. "cheap", "mid-range", "fine dining".
    Use this for food and drink only. It does not return opening hours or
    current prices, so do not promise either.
    """


    label, _, _, note= _geocode(city)

    query = f"Find places to eat in {label} for a {budget} budget"
    return note + _search(query)


@tool
def find_events(city: str, date: str = "", category: str = "", attraction_id: str="") -> str:
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
   

    label ,lat, long, note= _geocode(city)


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
        "localStartDateTime": local_start,
        "localEndDateTime": local_end,
        "size": 20,
    }
    if category:
        params["classificationName"] = category
    if attraction_id:
        params["attractionId"] = attraction_id
    
    response = requests.get(url = EVENTS_URL, params= params, timeout=20)


    if response.status_code==200:

        result = response.json().get("_embedded", {}).get("events",[])
        if not result:
            return "No events found"
        
        else:
            price_text = lambda e: (
                "price not published" if not e.get("priceRanges")
                else "price listed as 0 (placeholder, not free)"
                if e["priceRanges"][0].get("min", 0) == 0 and e["priceRanges"][0].get("max", 0) == 0
                else f"{e['priceRanges'][0].get('min')}–{e['priceRanges'][0].get('max')} {e['priceRanges'][0].get('currency', '')}"
            )
            
            lines = [
                f"- {event['name']} | "
                f"{event['dates']['start'].get('localDate', 'date TBA')} | "
                f"{event['_embedded']['venues'][0].get('name', 'Venue TBA')} | "
                f"{price_text(event)} | {event['url']}"
                for event in result
            ]
            message = "\n".join(lines)

        return f"Events in {label} are as follows: {message}. {note}"

    return f"Error: {response.status_code}"


@tool
def get_transit_info(city: str) -> str:
    """Get general information about getting around a city: metro, bus, passes, fares.

    This returns written guidance, NOT timetables and NOT journey times. If the
    user asks how many minutes a specific trip takes, say you cannot look that
    up rather than estimating it.
    """

    label, _, _, note= _geocode(city)

    query = f"How do you get around in the city of {city}. Exact location is {label}"

    return f"Transit information for {label}: {_search(query)}. {note}"



@tool
def find_performer_events(performer: str, city: str, date:str =""):
    """Get the details of a specific ticketed attraction.

    The attractions returned by the API are not tourist attractions. More like
    ticketed tourist exhibitions. An "attraction" in this API is a performer, team or touring show

    If the API returns "no such performer", please say so and do not guess anything.
    If the API returns "the performer exists but has nothing in that city", say it as is.
    If the input (either city or performer) is something ambigious and matches mutliple outputs, do not select
    just the first one, instead mention everything that comes up and confirm with the
    user which one of the options they meant. And then proceed with the planning

    This tool does not replace the find_attractions tool

    """

    attraction_params = {
        "apikey": os.getenv("TICKETMASTER_API_KEY"), 
        "keyword": performer
    }

    attraction_response = requests.get(url=ATTRACTIONS_URL, params=attraction_params, timeout=20)
    if attraction_response.status_code==200:
        attractions = attraction_response.json().get("_embedded", {}).get("attractions", [])

        if not attractions:
            return "No performer found"

        exact = [a for a in attractions if a["name"].lower() == performer.lower()]

        if len(exact) > 1:
            return f"Which one of these attractions did you mean? {','.join(exact)}"
        else:
            attraction_id = attractions[0]["id"]
            result = find_events.invoke({"city":city, "attractionId":attraction_id, "date": date})

            if result == "No events found":
                return f"{performer} exists but has nothing in {city} for that period"
            else:
                return result

    return f"Error: {attraction_response.status_code}"


    


TOOLS = [get_weather, get_transit_info, find_restaurants, find_events, find_attractions, find_performer_events]

    
