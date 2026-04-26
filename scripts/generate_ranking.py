#!/usr/bin/env python3
"""Generate frontend/ranking.json from the badges CSV and avatars index.
Run via: python3 scripts/generate_ranking.py
Also called automatically by: make deploy
"""

import csv
import json
import os
import re
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH      = os.path.join(ROOT, "src", "knowledge_base", "mapaches_badges.csv")
AVATARS_PATH  = os.path.join(ROOT, "src", "knowledge_base", "avatars.json")
NAME_MAP_PATH = os.path.join(ROOT, "src", "knowledge_base", "badge_name_map.json")
OUT_PATH      = os.path.join(ROOT, "frontend", "ranking.json")

LEVEL_PREFIX_RE = re.compile(r"^L\d+\s*-\s*")

# Mirrors _CASAS in src/rag.py — keep in sync
_CASAS = {
    "yuri ccasa": "Chavin", "martha aguero": "Chavin",
    "arturo arellano": "Chavin", "gracia dextre": "Chavin",
    "gustavo zambrano": "Chavin", "sandra lizardo": "Chavin",
    "luz chang navarro": "Chavin", "rodolfo mondion": "Chavin",
    "jose castañeda": "Chavin", "julyeth alcantara": "Chavin", "fernando ureta": "Chavin",
    "brissy cáceres": "Chavin", "brissy caceres": "Chavin",
    "jose del castillo": "Wari", "lucía guerrero": "Wari", "lucia guerrero": "Wari",
    "marco ramos": "Wari", "kailey nuñez": "Wari", "kai nuñez": "Wari",
    "rodrigo benza": "Wari", "camila gastelumendi": "Wari",
    "jorge cabeza": "Wari", "maria ines romero": "Wari", "mane romero": "Wari",
    "alvaro guerrero": "Wari", "evelyn quispe": "Wari",
    "nereo sanchez": "Wari", "luciana franco": "Wari",
    "juan antonio vasquez": "Moche", "juan antonio vásquez": "Moche",
    "marcia rivas": "Moche", "carla laredo": "Moche",
    "juana balvin": "Moche", "carlos jiménez": "Moche", "carlos jimenez": "Moche",
    "héctor montellano": "Moche", "hector montellano": "Moche",
    "alejandra delgadillo": "Moche", "luis rodriguez": "Moche",
    "linda concepción": "Moche", "linda concepcion": "Moche",
    "josé fajardo": "Moche", "jose fajardo": "Moche",
    "julio príncipe": "Nazca", "julio principe": "Nazca",
    "mónica salazar": "Nazca", "monica salazar": "Nazca",
    "raul gutierrez": "Nazca", "karenina alvarez": "Nazca",
    "morita rejas": "Nazca", "daniel mcbride": "Nazca",
    "gabriela valencia": "Nazca", "manuel rouillon": "Nazca",
    "pilar gárate": "Nazca", "pilar garate": "Nazca",
    "martín vegas": "Nazca", "martin vegas": "Nazca",
    "gina sare": "Nazca", "enrique hernández": "Nazca", "enrique hernandez": "Nazca",
    "fressia sánchez": "Nazca", "fressia sanchez": "Nazca",
    "pedro montoya": "Nazca", "diana díaz": "Nazca", "diana diaz": "Nazca",
}


def parse_date(earned: str) -> datetime:
    earned = earned.strip()
    if re.match(r"^\d{4}$", earned):
        return datetime(int(earned), 1, 1)
    try:
        return datetime.strptime(earned, "%m-%d-%y")
    except ValueError:
        return datetime.min


def main() -> None:
    with open(NAME_MAP_PATH, encoding="utf-8") as f:
        name_map = json.load(f)

    with open(AVATARS_PATH, encoding="utf-8") as f:
        avatars = json.load(f)

    cutoff = datetime.now() - timedelta(days=90)
    unique_badges: set[str] = set()
    learners: dict[str, dict] = {}  # display_name → {count, recent_count}

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            from_name  = row["From"].strip()
            badge_name = LEVEL_PREFIX_RE.sub("", row["Badge Name"].strip())
            badge_name = name_map.get(badge_name, badge_name)
            earned_dt  = parse_date(row["EARNED"])

            unique_badges.add(badge_name)

            if from_name not in learners:
                learners[from_name] = {"count": 0, "recent_count": 0}
            learners[from_name]["count"] += 1
            if earned_dt >= cutoff:
                learners[from_name]["recent_count"] += 1

    total = len(unique_badges)

    def build_entry(name: str, count: int, rank: int) -> dict:
        key = name.lower().strip()
        avatar_file = avatars.get(key)
        return {
            "rank":   rank,
            "name":   name,
            "casa":   _CASAS.get(key, ""),
            "avatar": f"mapache-fotos/{avatar_file}" if avatar_file else None,
            "count":  count,
            "pct":    round(count / total * 100) if total else 0,
        }

    all_time = [
        build_entry(name, d["count"], i + 1)
        for i, (name, d) in enumerate(sorted(learners.items(), key=lambda x: -x[1]["count"]))
    ]

    last_3_months = [
        build_entry(name, count, i + 1)
        for i, (name, count) in enumerate(
            sorted(
                [(n, d["recent_count"]) for n, d in learners.items() if d["recent_count"] > 0],
                key=lambda x: -x[1],
            )
        )
    ]

    output = {
        "generated_at":       datetime.now().strftime("%Y-%m-%d"),
        "total_unique_badges": total,
        "all_time":           all_time,
        "last_3_months":      last_3_months,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ ranking.json generado en {OUT_PATH}")
    print(f"  {len(all_time)} mapaches | {len(last_3_months)} con badges recientes | {total} badges únicos")


if __name__ == "__main__":
    main()
