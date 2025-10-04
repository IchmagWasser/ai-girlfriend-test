import json

# Dummy-Rezeptdatenbank
RECIPE_DB = {
    "spaghetti carbonara": {
        "zutaten": [
            {"name": "Spaghetti", "menge": 100, "einheit": "g", "preis_pro_einheit": 0.15},
            {"name": "Eier", "menge": 2, "einheit": "Stück", "preis_pro_einheit": 0.30},
            {"name": "Speck", "menge": 80, "einheit": "g", "preis_pro_einheit": 1.20},
            {"name": "Parmesan", "menge": 40, "einheit": "g", "preis_pro_einheit": 2.50},
        ]
    },
    "pizza margherita": {
        "zutaten": [
            {"name": "Pizzateig", "menge": 250, "einheit": "g", "preis_pro_einheit": 0.40},
            {"name": "Tomatensoße", "menge": 100, "einheit": "ml", "preis_pro_einheit": 0.60},
            {"name": "Mozzarella", "menge": 150, "einheit": "g", "preis_pro_einheit": 1.80},
        ]
    }
}

def generate_shopping_list(gericht: str, portionen: int) -> dict:
    gericht_lower = gericht.lower().strip()
    
    if gericht_lower in RECIPE_DB:
        recipe_data = RECIPE_DB[gericht_lower]
    else:
        # Fallback
        recipe_data = {
            "zutaten": [
                {"name": "Zutat 1", "menge": 100, "einheit": "g", "preis_pro_einheit": 1.00},
                {"name": "Zutat 2", "menge": 50, "einheit": "g", "preis_pro_einheit": 0.50},
            ]
        }
    
    zutaten = []
    gesamtkosten = 0.0
    
    for zutat in recipe_data["zutaten"]:
        menge_total = zutat["menge"] * portionen
        kosten = (zutat["menge"] * zutat["preis_pro_einheit"]) * portionen
        
        zutaten.append({
            "name": zutat["name"],
            "menge_total": menge_total,
            "einheit": zutat["einheit"],
            "kosten": round(kosten, 2)
        })
        
        gesamtkosten += kosten
    
    return {
        "gericht": gericht,
        "portionen": portionen,
        "zutaten": zutaten,
        "gesamtkosten": round(gesamtkosten, 2)
    }