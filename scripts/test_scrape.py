import requests
from bs4 import BeautifulSoup
import re

def clean_text(text):
    if not text:
        return None
    # Replace bracketed energy symbols first
    text = re.sub(r"\[([A-Z])\]", r"{\1}", text)
    # Split by lines and clean each line individually to preserve newlines
    lines = []
    for line in text.splitlines():
        cleaned_line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if cleaned_line:
            lines.append(cleaned_line)
    return "\n".join(lines)

def parse_limitless_card(url, supertype_hint):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # Pre-process <br> tags by replacing them with \n
    for br in soup.find_all("br"):
        br.replace_with("\n")

    # 1. Parse Weakness & Retreat Cost
    weakness_type = None
    retreat_cost = None
    wrr_el = soup.find(class_="card-text-wrr")
    if wrr_el:
        wrr_text = wrr_el.get_text()
        m_weakness = re.search(r"Weakness:\s*(\w+)", wrr_text)
        if m_weakness:
            weakness_type = m_weakness.group(1).strip()
        
        m_retreat = re.search(r"Retreat:\s*(\d+)", wrr_text)
        if m_retreat:
            retreat_cost = int(m_retreat.group(1).strip())

    # 2. Parse Trainer Subtype
    trainer_subtype = None
    supertype = "Pokemon"
    type_el = soup.find(class_="card-text-type")
    if type_el:
        type_text = type_el.get_text()
        if "Trainer" in type_text:
            supertype = "Trainer"
            if "-" in type_text:
                trainer_subtype = type_text.split("-")[-1].strip()
            else:
                trainer_subtype = "Item"  # default fallback
        else:
            supertype = "Pokemon"

    # 3. Parse Effect Text / Flavor Text
    effect_text = None
    if supertype == "Trainer":
        sections = soup.find_all(class_="card-text-section")
        cand = []
        for sec in sections:
            classes = sec.get("class", [])
            sec_text = sec.get_text()
            # Skip if it is the type section or the artist section or flavor text
            if "card-text-artist" in classes or "card-text-flavor" in classes:
                continue
            # A trainer type section contains "Trainer -"
            if "Trainer" in sec_text and ("Supporter" in sec_text or "Item" in sec_text or "Tool" in sec_text):
                continue
            cand.append(sec_text)
        if cand:
            effect_text = clean_text("\n".join(cand))
    else:
        # For Pokémon: get flavor text if present
        flavor_el = soup.find(class_="card-text-flavor")
        if flavor_el:
            effect_text = clean_text(flavor_el.get_text())

    return {
        "supertype": supertype,
        "weakness_type": weakness_type,
        "retreat_cost": retreat_cost,
        "trainer_subtype": trainer_subtype,
        "effect_text": effect_text
    }

if __name__ == "__main__":
    urls = [
        ("https://pocket.limitlesstcg.com/cards/A1/3", "Pokemon"),      # Venusaur ex
        ("https://pocket.limitlesstcg.com/cards/A1/220", "Trainer"),    # Misty
        ("https://pocket.limitlesstcg.com/cards/A1/216", "Trainer"),    # Helix Fossil
        ("https://pocket.limitlesstcg.com/cards/B3/8", "Pokemon"),       # Mega Sceptile ex
    ]
    for url, hint in urls:
        print(f"Scraping: {url}")
        res = parse_limitless_card(url, hint)
        print(res)
        print("-" * 40)
