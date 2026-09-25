#!/usr/bin/env python3
"""Seed script for Firestore with itinerary items for VoyageSync."""

from google.cloud import firestore

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-34e5dd261b72"

SEEDED_ITEMS = [
    {
        "id": "item-001",
        "title": "Golden Gate Bridge & Vista Point Walk",
        "category": "Sightseeing",
        "day": "Day 1",
        "time": "10:00 AM",
        "location": "Golden Gate Bridge, San Francisco, CA",
        "cost_per_person": 0.0,
        "description": "Group walk across the iconic suspension bridge with panoramic bay views and photo stops.",
        "notes": "Bring light jackets; windy and brisk on the bridge.",
    },
    {
        "id": "item-002",
        "title": "Ferry Building Food Tasting",
        "category": "Dining",
        "day": "Day 1",
        "time": "01:00 PM",
        "location": "Ferry Building Marketplace, San Francisco, CA",
        "cost_per_person": 35.0,
        "description": "Explore artisanal food stalls, fresh sourdough bread, artisan cheeses, and local bites.",
        "notes": "Great vegetarian and gluten-free options available.",
    },
    {
        "id": "item-003",
        "title": "Alcatraz Island Sunset Tour",
        "category": "Culture",
        "day": "Day 2",
        "time": "05:30 PM",
        "location": "Pier 33, San Francisco, CA",
        "cost_per_person": 65.0,
        "description": "Ferry cruise and award-winning audio tour of the historic prison island at sunset.",
        "notes": "Tickets reserved in advance; boarding starts 30 mins prior.",
    },
    {
        "id": "item-004",
        "title": "Muir Woods Redwood Canopy Hike",
        "category": "Adventure",
        "day": "Day 3",
        "time": "09:30 AM",
        "location": "Mill Valley, CA",
        "cost_per_person": 15.0,
        "description": "Scenic shaded loop trail through towering ancient old-growth coastal redwood trees.",
        "notes": "Parking reservation confirmed; cool morning temperatures expected.",
    },
]

def seed_database():
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    collection_ref = db.collection("itinerary_items")
    print(f"Seeding Firestore collection 'itinerary_items' in project '{FIRESTORE_PROJECT_ID}'...")

    for item in SEEDED_ITEMS:
        doc_id = item["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(item)
        print(f"  ✓ Seeded item: {doc_id} - {item['title']}")

    print("Seeding complete! Verifying items in collection:")
    docs = list(collection_ref.stream())
    print(f"  Total items in 'itinerary_items': {len(docs)}")

if __name__ == "__main__":
    seed_database()
