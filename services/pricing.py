def calculate_pricing(zutaten: list, zielmarge: float) -> dict:
    wareneinsatz = 0.0
    details = []
    
    for zutat in zutaten:
        kosten = zutat["menge"] * zutat["ek_preis"]
        wareneinsatz += kosten
        
        details.append({
            "name": zutat["name"],
            "menge": zutat["menge"],
            "ek_preis": zutat["ek_preis"],
            "kosten": round(kosten, 2)
        })
    
    vk_empfohlen = wareneinsatz * (1 + zielmarge / 100)
    gewinn = vk_empfohlen - wareneinsatz
    
    return {
        "wareneinsatz": round(wareneinsatz, 2),
        "zielmarge": zielmarge,
        "vk_empfohlen": round(vk_empfohlen, 2),
        "gewinn_pro_portion": round(gewinn, 2),
        "details": details
    }