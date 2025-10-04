import csv
import io
from datetime import datetime

def generate_csv_shopping_list(data: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Gericht', data['gericht']])
    writer.writerow(['Portionen', data['portionen']])
    writer.writerow([])
    writer.writerow(['Zutat', 'Menge', 'Einheit', 'Kosten (EUR)'])
    
    for zutat in data['zutaten']:
        writer.writerow([
            zutat['name'],
            zutat['menge_total'],
            zutat['einheit'],
            f"{zutat['kosten']:.2f}"
        ])
    
    writer.writerow([])
    writer.writerow(['Gesamtkosten', f"{data['gesamtkosten']:.2f} EUR"])
    
    return output.getvalue()

def generate_csv_pricing(data: dict, gericht: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Gericht', gericht])
    writer.writerow([])
    writer.writerow(['Zutat', 'Menge', 'EK-Preis', 'Kosten'])
    
    for detail in data['details']:
        writer.writerow([
            detail['name'],
            detail['menge'],
            f"{detail['ek_preis']:.2f}",
            f"{detail['kosten']:.2f}"
        ])
    
    writer.writerow([])
    writer.writerow(['Wareneinsatz', f"{data['wareneinsatz']:.2f} EUR"])
    writer.writerow(['Empfohlener VK', f"{data['vk_empfohlen']:.2f} EUR"])
    
    return output.getvalue()