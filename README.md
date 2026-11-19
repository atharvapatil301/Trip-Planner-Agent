# Trip Planner Agent

A conversational trip-planning agent that builds and refines day plans from live weather, event, and web-search data, then turns the conversation into a structured itinerary.

**Live demo:** https://trip-planner-agent-ris7.onrender.com/
*(Free-tier Render instance. The first request after it has been idle can take about 30 seconds.)*

```
You:   Plan me a day in Boston tomorrow — free things to see and a cheap lunch.
Agent: checks the forecast → finds sights → finds food → returns a plan with a source URL on every item
You:   Make it rainy-day friendly and add dinner.
Agent: adjusts the existing plan instead of starting over
       → "Build itinerary" turns the whole conversation into typed JSON
```

---

## Why this project is interesting

The hard part of a trip-planning agent is not calling APIs. It is **not making things up when the data runs out.** The agent is built around one rule: *if a tool didn't return it, the agent doesn't claim it.*

| Data gap | What a naive agent does | What this agent does |
|---|---|---|
| A date more than 16 days out (past the forecast window) | "Boston in November is usually cold and wet" | Says no forecast exists yet and names the dates that are available |
| An ambiguous city (`Cambridge` resolves to England, `Portland` to Oregon) | Plans a day in the wrong country | Flags the ambiguity and asks which one you meant |
| Ticket prices (fewer than 10% of Ticketmaster events publish one) | Invents a plausible price, or reads `0.0` as "free" | Reports "price not published" and treats a `0` price as a placeholder |
| Opening hours and journey times (no tool returns them) | "Opens at 10am", "about 15 minutes on the Red Line" | Says it can't verify them and points you to the source |
| A Ticketmaster category it doesn't recognize, which returns zero events and no error | "Nothing is on this weekend" | Notes that the filter may be the cause, not a quiet week |
| UTC vs. local-time date filters | Returns events from the night before | Queries in the venue's local time, so the events are on the day you asked about |
| A fuzzy performer search ("Taylor Swift" lists tribute bands first) | Reports the tribute band's tour dates | Uses only an exact name match, and asks you to choose when several acts match |

---

## Architecture

```
            ┌──────────────────────────────┐
  User ───▶ │  Gradio chat UI  (app.py)    │
            └──────────────┬───────────────┘
                           ▼
            ┌──────────────────────────────┐
            │  LangChain agent (Gemini)    │◀── system prompt (agent/prompts.py)
            │  + LangGraph InMemorySaver   │    multi-turn memory, one thread per browser session
            └──────────────┬───────────────┘
        ┌──────────┬───────┴──────┬───────────────┬───────────────┐
        ▼          ▼              ▼               ▼               ▼
   get_weather  find_events  find_attractions  find_restaurants  get_transit_info
   Open-Meteo   Ticketmaster    Tavily            Tavily           Tavily
   (structured) (structured)  (web prose)       (web prose)      (web prose)
                           │
                           ▼
            build_itinerary → Pydantic `Itinerary` (structured output)
```

### Tools

| Tool | Source | Returns |
|---|---|---|
| `get_weather(city, date)` | Open-Meteo geocoding + forecast (no key) | Conditions for that date, the resolved place name, and an ambiguity warning when another place shares the name |
| `find_events(city, date, category)` | Ticketmaster Discovery API | Event name, local date, venue, price (or "price not published"), and link |
| `find_attractions(city, interest)` | Tavily web search | Sights, museums, and parks, with source URLs |
| `find_restaurants(city, budget)` | Tavily web search | Places to eat, with source URLs |
| `get_transit_info(city)` | Tavily web search | General guidance on getting around (no timetables) |
| `find_performer_events(performer, city, date)` | Ticketmaster attractions → events | Chains two API calls to find where a specific act is playing. It tells apart "no such act", "several acts match, which one?", and "exists but isn't playing there" |

Design choices that apply to every tool:

- **Tools return readable strings and report failures in them.** The model can act on "No forecast for 2026-12-01, only 2026-11-19 to 2026-12-04 are available"; it can't act on a traceback.
- **Tools state what they don't contain.** Search results end with a reminder that the snippets have no opening hours and that a price only counts if it appears in the text.
- **Docstrings are the routing logic.** The model picks a tool from its docstring, so each one says what it is for and what it is *not* for.

### Structured itinerary

`agent/itinerary.py` defines Pydantic models (`Itinerary`, then `DayPlan`, then `Activity`). These are extracted from the conversation with `with_structured_output`. `Activity.cost` is a **string**, not a float, so `"unknown"` is a valid value and the model is never forced to invent a number. The itinerary also has an `unknowns` list for anything the tools couldn't confirm.

---

## Tech stack

- **LangChain 1.x** (`create_agent`, `@tool`) and **LangGraph** (`InMemorySaver` checkpointer)
- **Google Gemini** through `langchain-google-genai`. OpenRouter and NVIDIA endpoints are drop-in alternatives.
- **Pydantic v2** for structured output
- **Gradio** for the web UI
- **Render** for hosting
- APIs: **Open-Meteo**, **Ticketmaster Discovery v2**, **Tavily**

---

## Project structure

```
trip_planner/
  agent/
    agent.py       agent construction, model, memory, CLI loop
    tools.py       the six tools and their shared helpers (_search, _geocode)
    prompts.py     system prompt
    itinerary.py   Pydantic itinerary schema and build_itinerary()
  agentui.py       Gradio chat wrapper
  app.py           web entry point (chat UI + "Build itinerary" button)
  requirements.txt
test_prompts.md    behavioral test scenarios
```

---

## Running locally

**1. Install**

```bash
cd trip_planner
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**2. Add API keys** in `trip_planner/.env`:

```env
GOOGLE_API_KEY=...         # or another supported LLM vendor
TAVILY_API_KEY=...         # free tier at tavily.com
TICKETMASTER_API_KEY=...   # free at developer.ticketmaster.com (5,000 calls/day)
```

Open-Meteo needs no key.

**3. Run**

```bash
python -m agent.agent    # terminal chat; prints the itinerary JSON when you type 'quit'
python app.py            # web UI at http://127.0.0.1:7860
```

---

## Deployment (Render)

Deploy as a **Web Service**:

| Setting | Value |
|---|---|
| Root directory | `trip_planner` |
| Build command | `pip install -r requirements.txt` |
| Start command | `python app.py` |
| Environment variables | `GOOGLE_API_KEY`, `TAVILY_API_KEY`, `TICKETMASTER_API_KEY` |

`app.py` binds to `0.0.0.0` on Render's `$PORT`.

---

## Testing

[test_prompts.md](test_prompts.md) contains scenario prompts that exercise each failure mode above: baseline planning, refinement with memory, past-the-forecast dates, ambiguous cities, opening hours, journey times, cost totals, missing ticket prices, unknown categories, and UTC vs. local dates. Each scenario describes what a pass looks like and what a failure looks like.

---

## Limitations and next steps

- Memory is in-process (`InMemorySaver`), so conversations reset when the server restarts. Each browser session gets its own thread, so refreshing the page starts a new conversation. Old threads are never cleaned up.
- Weather is reduced to a condition summary. Temperature and precipitation probability would make the plans more useful.
- Possible next steps: a persistent checkpointer (SQLite or Postgres) and an automated eval harness that runs the scenarios in `test_prompts.md`.
