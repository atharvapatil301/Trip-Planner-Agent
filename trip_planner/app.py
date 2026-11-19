import os

import gradio as gr

from agentui import GradioUI, session_config

from agent.agent import model, trip_agent
from agent.itinerary import build_itinerary


def make_itinerary(request: gr.Request):
    """Read this session's conversation out of the checkpointer and extract the plan."""
    state = trip_agent.get_state(session_config(request))
    messages = state.values.get("messages", [])
    if not messages:
        return {"note": "Plan something in the chat first, then press this."}
    try:
        plan = build_itinerary(model, {"messages": messages})
    except Exception as e:  # noqa: BLE001 - surface it in the UI, don't 500
        return {"error": f"Could not build the itinerary: {e}"}
    return plan.model_dump(mode="json")


ui = GradioUI(trip_agent, title="Trip Planner")  # one thread per browser session

# Take the ChatInterface as it comes, then REOPEN it to append our button.
# `with demo:` re-enters the existing Blocks context. Building the chat inside a
# fresh gr.Blocks() instead looks identical on screen and is broken: the
# ChatInterface's own submit wiring is lost, and the chat never answers.
demo = ui.interface()

with demo:
    gr.Markdown(
        "### Structured itinerary\n"
    "Press this button to generate the structured itinerary from the conversation."
    )
    build_button = gr.Button("Build itinerary from this conversation",
                             variant="primary")
    plan_output = gr.JSON(label="Itinerary")
    build_button.click(make_itinerary, outputs=plan_output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0",
                server_port=int(os.environ.get("PORT", 7860)))
