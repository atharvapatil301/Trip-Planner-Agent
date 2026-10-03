"""Trip planner: the structured itinerary.

To be used to extract one typed itinerary from the conversation.

Please note `cost` is a STRING, not a number, so that
"unknown" is something the itinerary can actually say, and so a price can carry
where it came from. If it were a float the model would have to invent a figure
to fill it, and a made-up price looks exactly like a real one.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Activity(BaseModel):
    """One thing the traveller does."""
    time: str = Field(description="When it starts, e.g. '09:30' or 'morning'")
    name: str = Field(description="What it is, e.g. 'Museum of Fine Arts'")
    kind: Literal["sight", "food", "event", "travel", "other"] = Field(
        description="Which tool this came from: sight, food, event, travel, other")
    cost: str = Field(
        description="'free' only when something actually says it is free; a price "
                    "a tool gave you, marked with where it came from, like "
                    "'$45-120 (Ticketmaster)' or '$27 (blog, unconfirmed)'; or "
                    "'unknown'. Most things will be 'unknown' — that is the honest "
                    "answer, and an unknown price is never 0.")
    source: str = Field(description="The URL this came from, or 'none' if there isn't one")



class DayPlan(BaseModel):
    """One day of the trip."""

    date: str = Field(description="What date is it on that day (in YYYY-MM-DD format), e.g. 2026-01-04")
    weather: str = Field(description="The weather forecast for a city on a date")
    ativity: list[Activity] = Field(description="List of activities")




class Itinerary(BaseModel):
    """A complete plan for a trip."""

    city: str = Field(description="The city the traveller plans to visit")
    daysplan: list[DayPlan] = Field(description="A list of days, each holding a list of activities")
    unknowns: list[dict] = Field(description="A list of the output of the tools that could not confirm e.g. could not lookup something or No forecast available")


ITINERARIES = []


def build_itinerary(model, result) -> Itinerary:
    """Extract a structured itinerary from a finished planning conversation.

    This is `file_ticket` from W03b.1 pointed at a trip. Build the transcript from
    result["messages"], call model.with_structured_output(Itinerary) on it, append
    to ITINERARIES, and return it.
    """

    transcript = "\n".join(f"{type(m).__name__}: {m.text}" for m in result["messages"] if m.text)
    record = model.with_structured_output(Itinerary).invoke(
        f"Create an Itinerary record from the transcript:\n\n{transcript}")
    ITINERARIES.append(record)
    return record 


