# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import (
    AgentEngineSandboxCodeExecutor,
)
from google.adk.memory.vertex_ai_memory_bank_service import (
    VertexAiMemoryBankService,
)
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback


MODEL = "gemini-3.6-flash"

# Hardcoded project ID as required for Agent Platform / Firestore / Storage clients
PROJECT_ID = "qwiklabs-gcp-04-34e5dd261b72"
GCS_BUCKET_NAME = "voyagesync-media-34e5dd26"
SANDBOX_RESOURCE_NAME = os.environ.get(
    "AGENT_ENGINE_SANDBOX_RESOURCE_NAME",
    "projects/715196689108/locations/us-east1/reasoningEngines/6958965222845448192/sandboxEnvironments/1852228492058427392",
)
MEMORY_BANK_ID = "6958965222845448192"
MEMORY_BANK_LOCATION = "us-east1"


def memory_service_builder() -> VertexAiMemoryBankService:
    """Builds VertexAiMemoryBankService for deployed runtime."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location=MEMORY_BANK_LOCATION,
        agent_engine_id=MEMORY_BANK_ID,
    )


# WRITE: after each turn, send the session to Memory Bank for extraction.
async def generate_memories_callback(callback_context: CallbackContext) -> None:
    """Sends completed turn session to Vertex AI Memory Bank for fact and preference extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Notice: Memory generation callback encountered: {e}")
    return None

_firestore_client: firestore.Client | None = None
_storage_client: storage.Client | None = None
_image_gen_client: genai.Client | None = None


def get_firestore_client() -> firestore.Client:
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project=PROJECT_ID)
    return _firestore_client


def get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=PROJECT_ID)
    return _storage_client


def get_image_gen_client() -> genai.Client:
    global _image_gen_client
    if _image_gen_client is None:
        _image_gen_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
        )
    return _image_gen_client


def list_itinerary_items(category: str = "", day: str = "") -> list[dict[str, Any]]:
    """Lists itinerary activities and events from the Firestore trip database.

    Args:
        category: Optional filter by category (e.g. 'Sightseeing', 'Dining', 'Culture', 'Adventure'). Pass empty string for no category filter.
        day: Optional filter by day (e.g. 'Day 1', 'Day 2', 'Day 3'). Pass empty string for no day filter.

    Returns:
        A list of itinerary items with details including title, category, day, time, location, cost_per_person, description, and notes.
    """
    db = get_firestore_client()
    collection_ref = db.collection("itinerary_items")
    query = collection_ref

    if category:
        query = query.where("category", "==", category)
    if day:
        query = query.where("day", "==", day)

    items = []
    for doc in query.stream():
        data = doc.to_dict()
        data["id"] = doc.id
        items.append(data)
    return items


def add_itinerary_item(
    title: str,
    category: str,
    day: str,
    time: str,
    location: str,
    cost_per_person: float = 0.0,
    description: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Adds a new activity or event to the trip itinerary in Firestore.

    Args:
        title: The title or name of the activity (e.g. 'Golden Gate Park Picnic').
        category: The category (e.g. 'Sightseeing', 'Dining', 'Adventure', 'Culture').
        day: The day of the trip (e.g. 'Day 1', 'Day 2', 'Day 3').
        time: Time or time range for the activity (e.g. '02:00 PM', '10:00 AM - 12:00 PM').
        location: Physical location or venue address.
        cost_per_person: Estimated cost per person in USD (0.0 if free).
        description: Brief description of the activity or what the group will do.
        notes: Any practical tips or logistics (e.g. packing needs, reservations, transport).

    Returns:
        The created itinerary item record with its unique ID.
    """
    db = get_firestore_client()
    doc_id = f"item-{uuid.uuid4().hex[:6]}"
    item_data = {
        "id": doc_id,
        "title": title,
        "category": category,
        "day": day,
        "time": time,
        "location": location,
        "cost_per_person": float(cost_per_person),
        "description": description,
        "notes": notes,
    }
    db.collection("itinerary_items").document(doc_id).set(item_data)
    return item_data


def get_itinerary_item(item_id: str) -> dict[str, Any]:
    """Fetches full details of a specific itinerary item by ID.

    Args:
        item_id: The unique ID of the itinerary item (e.g. 'item-001').

    Returns:
        The itinerary item data if found, or an error dictionary.
    """
    db = get_firestore_client()
    doc = db.collection("itinerary_items").document(item_id).get()
    if not doc.exists:
        return {"error": f"Itinerary item '{item_id}' not found."}
    data = doc.to_dict()
    data["id"] = doc.id
    return data


def get_weather(location: str) -> str:
    """Fetches real-time live weather conditions, temperature, humidity, and wind speed for any city or destination.

    Args:
        location: The name of the city or location (e.g., 'San Francisco', 'New York', 'Paris', 'Tokyo').

    Returns:
        A string containing real live weather conditions and temperature.
    """
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "VoyageSync/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            geo_data = json.loads(resp.read().decode())

        if not geo_data.get("results"):
            return f"Could not find coordinates for location: {location}"

        loc = geo_data["results"][0]
        lat, lon, name = loc["latitude"], loc["longitude"], loc["name"]
        country = loc.get("country", "")

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m"
            "&temperature_unit=fahrenheit&wind_speed_unit=mph"
        )
        req = urllib.request.Request(weather_url, headers={"User-Agent": "VoyageSync/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            w_data = json.loads(resp.read().decode())

        curr = w_data.get("current", {})
        temp = curr.get("temperature_2m")
        humidity = curr.get("relative_humidity_2m")
        wind = curr.get("wind_speed_10m")
        code = curr.get("weather_code", 0)

        conditions = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Foggy",
            51: "Light drizzle",
            61: "Rain",
            71: "Snow",
            80: "Rain showers",
            95: "Thunderstorm",
        }
        condition_desc = conditions.get(code, "Clear")
        return (
            f"Live weather for {name}, {country}: {temp}°F, {condition_desc}, "
            f"Humidity: {humidity}%, Wind: {wind} mph."
        )
    except Exception as e:
        return f"Unable to retrieve live weather for {location} at this time: {str(e)}"


def search_places_and_attractions(query: str, limit: int = 4) -> list[dict[str, Any]]:
    """Searches for real-world attractions, landmarks, venues, and points of interest using the public OpenStreetMap Nominatim API.

    Args:
        query: The place, attraction, or venue to search for (e.g., 'Alcatraz San Francisco', 'Central Park NYC', 'Eiffel Tower Paris').
        limit: Maximum number of search results to return (default 4, max 10).

    Returns:
        A list of matching locations with display name, category, place type, latitude, and longitude.
    """
    try:
        capped_limit = min(max(1, limit), 10)
        encoded_query = urllib.parse.quote(query)
        url = f"https://nominatim.openstreetmap.org/search?q={encoded_query}&format=json&addressdetails=1&limit={capped_limit}"

        user_agent = os.environ.get("NOMINATIM_USER_AGENT", "VoyageSync-Planner/1.0 (google-workshop-travel-agent)")
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())

        results = []
        for item in data:
            results.append({
                "name": item.get("display_name"),
                "category": item.get("class", "general"),
                "type": item.get("type", "landmark"),
                "latitude": item.get("lat"),
                "longitude": item.get("lon"),
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to search places for '{query}': {str(e)}"}]


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


async def generate_trip_image(prompt: str, tool_context: ToolContext) -> str:
    """Generates an image (such as a postcard-style trip banner, event cover, or destination highlight visual) for an activity or itinerary item using the gemini-3.1-flash-lite-image model in the global region.
    Saves the image as an artifact for the Playground's Artifacts panel, uploads it to the public Cloud Storage bucket, and returns its public HTTPS URL.

    Args:
        prompt: Detailed visual prompt describing the scene, destination, or event banner to generate.
        tool_context: The ADK ToolContext used to save the artifact for the session.

    Returns:
        The public HTTPS URL of the uploaded image in Cloud Storage.
    """
    try:
        client = get_image_gen_client()
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        for part in response.parts:
            if part.inline_data and part.inline_data.data:
                image_bytes = part.inline_data.data
                if part.inline_data.mime_type:
                    mime_type = part.inline_data.mime_type
                break

        if not image_bytes:
            return "Error: No image was generated by the model."

        ext = "jpg" if "jpeg" in mime_type or "jpg" in mime_type else "png"
        filename = f"trip_banner_{uuid.uuid4().hex[:8]}.{ext}"

        # (1) Save artifact for Playground Artifacts panel
        try:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
        except Exception as e:
            print(f"Notice: Artifact saving encountered: {e}")

        # (2) Upload to public Cloud Storage bucket
        storage_cli = get_storage_client()
        bucket = storage_cli.bucket(GCS_BUCKET_NAME)
        blob_path = f"banners/{filename}"
        blob = bucket.blob(blob_path)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{blob_path}"
        return public_url
    except Exception as e:
        return f"Failed to generate and upload image: {str(e)}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are VoyageSync, an intelligent trip and group event planner concierge. "
        "You help families and teams organize, view, and manage upcoming travel itineraries. "
        "You remember the user's stated preferences, dietary needs, party details, and facts from previous conversations and use them to personalize your recommendations. "
        "Always use your tools to query or add activities in the Firestore itinerary database, "
        "look up real-world attractions and venues with search_places_and_attractions, "
        "generate postcard banners or event visuals using generate_trip_image, "
        "and use your Python code execution sandbox to safely perform calculations, budget analysis, or data manipulation. "
        "Answer questions about weather, time, and scheduling accurately and concisely."
    ),
    workflow_description=(
        "For simple conversational greetings and chit-chat (e.g. 'Hi', 'Hello'), reply with polite plain text. "
        "When presenting itineraries, activities, places, budgets, or visual cards, return structured A2UI."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=AgentEngineSandboxCodeExecutor(
        sandbox_resource_name=SANDBOX_RESOURCE_NAME,
    ),
    tools=[
        PreloadMemoryTool(),
        list_itinerary_items,
        add_itinerary_item,
        get_itinerary_item,
        search_places_and_attractions,
        generate_trip_image,
        get_weather,
        get_current_time,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
