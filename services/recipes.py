import random

RECIPE_TEMPLATES = [
    {
        "name": "Mediterrane Gemüsepfanne",
        "kategorie": "vegetarisch",
        "zutaten_basis": ["Paprika", "Zucchini", "Tomaten"],
        "schritte": [
            "Gemüse schneiden",
            "In Pfanne anbraten",
            "Mit Gewürzen abschmecken"
        ]
    },
    {
        "name": "Pasta Pomodoro",
        "kategorie": "vegetarisch",
        "zutaten_basis": ["Pasta", "Tomaten", "Knoblauch", "Basilikum"],
        "schritte": [
            "Pasta kochen",
            "Tomatensoße zubereiten",
            "Vermischen und servieren"
        ]
    }
]

def generate_recipes(zutaten: str, vegetarisch: bool = False, vegan: bool = False) -> list:
    anzahl = min(2, len(RECIPE_TEMPLATES))
    recipes = random.sample(RECIPE_TEMPLATES, anzahl)
    
    for recipe in recipes:
        recipe["naehrwerte"] = {
            "kalorien": random.randint(250, 600),
            "protein": random.randint(10, 35),
            "kohlenhydrate": random.randint(20, 80),
            "fett": random.randint(5, 25)
        }
    
    return recipes