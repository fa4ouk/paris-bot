"""
🏆 Agent Parieur Pro - Version Double API
Football-Data.org pour les matchs + The Odds API pour les cotes
Matchs du jour uniquement
"""

import requests
import json
from datetime import datetime, timedelta
import os
import base64
from dateutil import parser

# Lecture depuis variables d'environnement (GitHub Actions)
# ou depuis config.py en local
try:
    from config import (
        FOOTBALL_DATA_API_KEY, ODDS_API_KEY, GROQ_API_KEY,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        COTE_MIN, COTE_MAX
    )
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")
except ImportError:
    FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    COTE_MIN = 1.4
    COTE_MAX = 2.5
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")

# 🏆 Championnats avec leurs IDs Football-Data.org
COMPETITIONS = {
    "PL": "Premier League",           # Premier League
    "PD": "La Liga",                  # La Liga
    "FL1": "Ligue 1",                 # Ligue 1
    "BL1": "Bundesliga",              # Bundesliga
    "SA": "Serie A",                  # Serie A
    "CL": "Champions League",         # Champions League
}

# Mapping des noms de compétitions pour The Odds API
ODDS_SPORT_MAPPING = {
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Ligue 1": "soccer_france_ligue_one",
    "Bundesliga": "soccer_germany_bundesliga",
    "Serie A": "soccer_italy_serie_a",
    "Champions League": "soccer_uefa_champions_league",
}

BASE_URL_FOOTBALL = "https://api.football-data.org/v4"
BASE_URL_ODDS = "https://api.the-odds-api.com/v4"
MARCHES = "h2h,totals,spreads"


# ─────────────────────────────────────────
# 1. RÉCUPÉRATION DES MATCHS DU JOUR UNIQUEMENT
# ─────────────────────────────────────────

def get_matches_by_competition(competition_code: str) -> list:
    """Récupère les matchs du jour pour une compétition"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    url = f"{BASE_URL_FOOTBALL}/competitions/{competition_code}/matches"
    params = {
        "dateFrom": today,
        "dateTo": today,  # UNIQUEMENT AUJOURD'HUI
        "status": "SCHEDULED",
    }
    headers = {
        "X-Auth-Token": FOOTBALL_DATA_API_KEY,
    }
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            matches = data.get("matches", [])
            # Ajoute l'info de la compétition
            for match in matches:
                match["_competition_code"] = competition_code
                match["_competition_name"] = COMPETITIONS.get(competition_code, competition_code)
                match["_sport"] = "soccer"
                # Ajoute le nom de la compétition pour l'API Odds
                match["_odds_sport"] = ODDS_SPORT_MAPPING.get(
                    COMPETITIONS.get(competition_code, ""), 
                    ""
                )
            return matches
        else:
            print(f"   ⚠️  {competition_code} ({COMPETITIONS.get(competition_code, '?')}): {r.status_code}")
            if r.status_code == 401:
                print("   ❌ Clé API Football-Data invalide !")
            return []
    except Exception as e:
        print(f"   ❌ {competition_code}: {e}")
        return []


def collect_matches() -> list:
    """Collecte les matchs du jour pour toutes les compétitions"""
    all_matches = []
    print("📡 Collecte des matchs du jour...")
    today = datetime.now().strftime("%d/%m/%Y")
    print(f"   📅 Date : {today}")
    print(f"   🏆 Compétitions actives: {len(COMPETITIONS)}")
    
    for code, name in COMPETITIONS.items():
        matches = get_matches_by_competition(code)
        if matches:
            all_matches.extend(matches)
            print(f"   ✅ {name} ({code}): {len(matches)} matchs")
        else:
            print(f"   ⚠️  {name} ({code}): 0 matchs aujourd'hui")
    
    print(f"📊 Total : {len(all_matches)} matchs du jour")
    return all_matches


# ─────────────────────────────────────────
# 2. RÉCUPÉRATION DES COTES DEPUIS THE ODDS API
# ─────────────────────────────────────────

def get_odds_for_match(home_team: str, away_team: str, competition: str) -> dict:
    """Récupère les cotes pour un match spécifique"""
    # Récupère le sport key
    sport_key = ODDS_SPORT_MAPPING.get(competition, "")
    if not sport_key:
        return {}
    
    url = f"{BASE_URL_ODDS}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": MARCHES,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            matches = r.json()
            # Cherche le match correspondant
            for match in matches:
                if (match.get("home_team") == home_team and 
                    match.get("away_team") == away_team):
                    # Extrait les meilleures cotes
                    best_odds = extract_best_odds(match, home_team, away_team)
                    return best_odds
        return {}
    except Exception as e:
        print(f"   ⚠️  Erreur cotes pour {home_team} vs {away_team}: {e}")
        return {}


def extract_best_odds(match: dict, home_team: str, away_team: str) -> dict:
    """Extrait les meilleures cotes pour un match"""
    best = {}
    
    for bookie in match.get("bookmakers", []):
        bookie_name = bookie.get("title", "?")
        for market in bookie.get("markets", []):
            mkey = market.get("key", "")
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "?")
                cote = outcome.get("price", 0)
                point = outcome.get("point", None)
                
                # Label lisible
                if mkey == "h2h":
                    if name == home_team:
                        label = f"Victoire {home_team}"
                    elif name == away_team:
                        label = f"Victoire {away_team}"
                    else:
                        label = "Match Nul"
                elif mkey == "totals":
                    direction = "Plus" if name == "Over" else "Moins"
                    label = f"{direction} de {point}"
                elif mkey == "spreads":
                    label = f"Handicap {name} {point}"
                else:
                    label = f"{name}"
                
                uid = f"{mkey}_{name}_{point}"
                if cote > 0:  # Garde toutes les cotes
                    if uid not in best or cote > best[uid]["cote"]:
                        best[uid] = {
                            "label": label,
                            "cote": cote,
                            "bookmaker": bookie_name,
                            "market": mkey,
                        }
    
    return best


# ─────────────────────────────────────────
# 3. PRÉPARATION DES DONNÉES POUR L'IA
# ─────────────────────────────────────────

def format_heure(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # Tunisie = UTC+1
        tz_tunis = timezone(timedelta(hours=1))
        dt_local = dt.astimezone(tz_tunis)
        return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return iso_str


def prepare_data(matches: list) -> list:
    """Prépare les données avec les cotes"""
    data = []
    tz_tunis = timezone(timedelta(hours=1))
    maintenant = datetime.now(tz_tunis)
    aujourd_hui = maintenant.date()
    
    print("\n🔍 Récupération des cotes...")
    
    for idx, match in enumerate(matches, 1):
        try:
            home = match.get("homeTeam", {}).get("name", "?")
            away = match.get("awayTeam", {}).get("name", "?")
            competition = match.get("_competition_name", "?")
            heure = format_heure(match.get("utcDate", ""))
            
            # Récupère les cotes pour ce match
            print(f"   [{idx}/{len(matches)}] {home} vs {away}...", end=" ")
            best_odds = get_odds_for_match(home, away, competition)
            
            if best_odds:
                print(f"✅ {len(best_odds)} cotes trouvées")
                # Filtre selon COTE_MIN et COTE_MAX
                filtered_odds = []
                for key, odds in best_odds.items():
                    if COTE_MIN <= odds["cote"] <= COTE_MAX:
                        filtered_odds.append(odds)
                
                if filtered_odds:
                    data.append({
                        "match": f"{home} vs {away}",
                        "competition": competition,
                        "heure": heure,
                        "selections": filtered_odds,
                    })
                else:
                    print(f"⚠️  Aucune cote dans la plage {COTE_MIN}→{COTE_MAX}")
            else:
                print("❌ Aucune cote trouvée")
                
        except Exception as e:
            print(f"❌ Erreur: {e}")
            continue
    
    return data


# ─────────────────────────────────────────
# 4. LECTURE DE L'HISTORIQUE
# ─────────────────────────────────────────

def get_historique() -> dict:
    """Récupère et analyse l'historique des paris depuis GitHub."""
    if not GH_TOKEN or not GH_REPO:
        return {}
    try:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/historique.json"
        r = requests.get(url, headers={
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }, timeout=10)
        if r.status_code != 200:
            return {}

        content = json.loads(base64.b64decode(r.json()["content"]).decode())
        paris = content.get("paris", [])
        if not paris:
            return {}

        # Analyse par type
        types = ["ULTRA SAFE", "VALEUR", "OPPORTUNISTE"]
        analyse = {}
        for t in types:
            subset = [p for p in paris if p.get("type") == t]
            gagnes = [p for p in subset if p.get("resultat") == "gagné"]
            taux = round(len(gagnes) / len(subset) * 100) if subset else None
            analyse[t] = {"total": len(subset), "gagnes": len(gagnes), "taux": taux}

        # Analyse par marché
        marches = {}
        for p in paris:
            sel = p.get("selection", "")
            if "Plus de" in sel or "Moins de" in sel:
                m = "over_under"
            elif "Victoire" in sel:
                m = "1x2"
            elif "Nul" in sel:
                m = "nul"
            else:
                m = "autre"
            if m not in marches:
                marches[m] = {"total": 0, "gagnes": 0}
            marches[m]["total"] += 1
            if p.get("resultat") == "gagné":
                marches[m]["gagnes"] += 1

        for m in marches:
            t = marches[m]["total"]
            marches[m]["taux"] = round(marches[m]["gagnes"] / t * 100) if t else 0

        stats = content.get("stats", {})

        return {
            "total_paris": len(paris),
            "taux_global": stats.get("taux", 0),
            "par_type": analyse,
            "par_marche": marches,
            "derniers_paris": paris[:5],
        }
    except Exception as e:
        print(f"⚠️  Impossible de lire l'historique : {e}")
        return {}


# ─────────────────────────────────────────
# 5. ANALYSE PAR L'IA (Groq)
# ─────────────────────────────────────────

def analyze_with_ai(data: list, historique: dict = None) -> str:
    if not data:
        return None

    if historique is None:
        historique = {}

    # Compression pour rester sous la limite de tokens
    compressed = []
    for m in data[:25]:
        compressed.append({
            "m": m["match"],
            "c": m["competition"],
            "h": m["heure"],
            "s": [{"l": s["label"], "c": s["cote"], "b": s["bookmaker"]} for s in m["selections"][:8]],
        })

    summary = json.dumps(compressed, ensure_ascii=False, separators=(",", ":"))

    # Prépare le contexte historique
    histo_context = ""
    if historique and historique.get("total_paris", 0) > 0:
        h = historique
        histo_context = f"""
HISTORIQUE DE TES PERFORMANCES ({h['total_paris']} pronostics) :
- Taux de réussite global : {h['taux_global']}%

Par type :"""
        for t, stats in h.get("par_type", {}).items():
            if stats["total"] > 0:
                histo_context += f"\n  • {t}: {stats['taux']}% ({stats['gagnes']}/{stats['total']})"

        histo_context += "\n\nPar marché :"
        for m, stats in h.get("par_marche", {}).items():
            if stats["total"] > 0:
                histo_context += f"\n  • {m}: {stats['taux']}% ({stats['total']} paris)"

        histo_context += """

RÈGLES D'ADAPTATION basées sur l'historique :
- Si taux < 50% sur un type → sois plus sélectif
- Si taux > 70% sur un type → tu peux être plus généreux
- Évite les marchés où tu te trompes souvent
- Mentionne dans ta note comment tu adaptes ta stratégie
"""
    else:
        histo_context = "Pas encore d'historique disponible — stratégie prudente par défaut."

    prompt = f"""Tu es un pronostiqueur sportif professionnel.

{histo_context}

Matchs du jour (cotes entre {COTE_MIN} et {COTE_MAX}) :
{summary}

Génère les meilleurs pronostics du jour en JSON uniquement, sans texte autour :

{{
  "paris": [
    {{
      "type": "ULTRA SAFE" ou "VALEUR" ou "OPPORTUNISTE",
      "style": "Simple" ou "Combiné",
      "match": "...",
      "competition": "...",
      "heure": "...",
      "selection": "...",
      "cote": 1.XX,
      "bookmaker": "...",
      "ev_pct": XX.X,
      "raison": "..."
    }}
  ],
  "note_du_jour": "...",
  "confiance": "Faible" ou "Moyen" ou "Élevé"
}}

RÈGLES :
- 2 à 5 pronostics max
- ULTRA SAFE = proba >70%
- VALEUR = cote sous-évaluée
- OPPORTUNISTE = combiné 2-3 sélections max
- Priorise les championnats européens
- Si journée pauvre, 1-2 pronostics ULTRA SAFE
- Réponds UNIQUEMENT avec le JSON"""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "max_tokens": 1500,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        else:
            print(f"❌ Erreur Groq : {r.status_code} — {r.text[:200]}")
            return None
    except Exception as e:
        print(f"❌ Erreur IA : {e}")
        return None


# ─────────────────────────────────────────
# 6. FORMATAGE DU MESSAGE TELEGRAM
# ─────────────────────────────────────────

TYPE_EMOJI = {
    "ULTRA SAFE": "🛡️",
    "VALEUR": "💎",
    "OPPORTUNISTE": "🎯",
}

SPORT_EMOJI = {
    "Premier League": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "La Liga": "🇪🇸",
    "Ligue 1": "🇫🇷",
    "Bundesliga": "🇩🇪",
    "Serie A": "🇮🇹",
    "Champions League": "🇪🇺",
}


def build_message(result: dict) -> str:
    paris = result.get("paris", [])
    note = result.get("note_du_jour", "")
    confiance = result.get("confiance", "?")

    now = datetime.now().strftime("%d/%m/%Y - %Hh%M")

    lines = []
    lines.append(f"📅 {now}")
    lines.append(f"📊 Confiance du jour : {confiance}")
    lines.append("")

    for i, pari in enumerate(paris, 1):
        ptype = pari.get("type", "PRONO")
        emoji_type = TYPE_EMOJI.get(ptype, "⚽️")
        comp = pari.get("competition", "")
        
        emoji_sport = "⚽️"
        for key, emoji in SPORT_EMOJI.items():
            if key.lower() in comp.lower():
                emoji_sport = emoji
                break

        lines.append(f"{emoji_type} PRONO {i} — {ptype}")
        lines.append(f"📋 {pari.get('match', '?')}")
        lines.append(f"🏆 {comp} {emoji_sport}")
        lines.append(f"⏰ {pari.get('heure', '?')}")
        lines.append(f"🎲 {pari.get('selection', '?')} ({pari.get('style', '')})")
        lines.append(f"📉 Cote : {pari.get('cote', '?')} sur {pari.get('bookmaker', '?')}")
        lines.append(f"📈 EV estimé : +{pari.get('ev_pct', '?')}%")
        lines.append(f"💡 {pari.get('raison', '')}")
        lines.append("")

    if note:
        lines.append(f"📝 {note}")
    lines.append("")
    lines.append("⚠️ Pronostics à titre informatif. Aucune garantie.")

    return "\n".join(lines)


# ─────────────────────────────────────────
# 7. PUSH VERS GITHUB
# ─────────────────────────────────────────

def push_to_github(result: dict):
    if not GH_TOKEN or not GH_REPO:
        print("⚠️  GH_TOKEN ou GH_REPO manquant")
        return

    url = f"https://api.github.com/repos/{GH_REPO}/contents/paris.json"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    content_str = json.dumps(result, ensure_ascii=False, indent=2)
    content_b64 = base64.b64encode(content_str.encode()).decode()

    payload = {
        "message": f"🏆 Paris du {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(url, headers=headers, json=payload, timeout=15)
        if r.status_code in [200, 201]:
            print("✅ paris.json mis à jour sur GitHub !")
        else:
            print(f"❌ Erreur GitHub : {r.status_code}")
    except Exception as e:
        print(f"❌ Erreur push GitHub : {e}")


# ─────────────────────────────────────────
# 8. ENVOI TELEGRAM
# ─────────────────────────────────────────

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "disable_web_page_preview": True,
            }, timeout=10)
            if r.status_code == 200:
                print("✅ Message envoyé sur Telegram !")
            else:
                print(f"❌ Telegram : {r.status_code}")
        except Exception as e:
            print(f"❌ Telegram : {e}")


# ─────────────────────────────────────────
# 9. MAIN
# ─────────────────────────────────────────

def main():
    print("=" * 50)
    print("🏆 AGENT PARIEUR PRO — Démarrage")
    print(f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"🎯 Cotes : {COTE_MIN}→{COTE_MAX}")
    print("🏆 Compétitions actives:")
    for code, name in COMPETITIONS.items():
        print(f"   - {name} ({code})")
    print("=" * 50)

    # Collecte des matchs du jour
    matches = collect_matches()
    if not matches:
        send_telegram("⚠️ Aucun match du jour disponible.")
        return

    # Préparation avec cotes
    data = prepare_data(matches)
    print(f"\n📊 {len(data)} matchs avec cotes dans la plage {COTE_MIN}→{COTE_MAX}")

    if not data:
        send_telegram(f"📋 Aucune cote entre {COTE_MIN} et {COTE_MAX} aujourd'hui.")
        return

    # Lecture historique
    print("\n📚 Lecture de l'historique...")
    historique = get_historique()
    if historique.get("total_paris", 0) > 0:
        print(f"   {historique['total_paris']} pronostics | Taux : {historique['taux_global']}%")
    else:
        print("   Pas encore d'historique")

    # Analyse IA
    print("\n🤖 Analyse IA...")
    raw = analyze_with_ai(data, historique)

    if not raw:
        send_telegram("❌ Erreur analyse IA.")
        return

    # Parse JSON
    try:
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
    except Exception as e:
        print(f"❌ Erreur parsing JSON : {e}")
        send_telegram(f"❌ Erreur de parsing.\n{raw[:500]}")
        return

    # Session ID
    now_tunis = datetime.now()
    moment = "matin" if now_tunis.hour < 15 else "soir"
    session_id = f"{now_tunis.strftime('%Y-%m-%d')}_{moment}"
    date_generation = now_tunis.isoformat()
    for p in result.get("paris", []):
        p["session_id"] = session_id
        p["date_generation"] = date_generation

    # Sauvegarde
    with open("paris_du_jour.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("💾 Résultats sauvegardés")

    # Push GitHub
    print("\n📤 Push vers GitHub...")
    push_to_github(result)

    # Envoi Telegram
    message = build_message(result)
    print("\n--- APERÇU ---")
    print(message)
    print("\n📱 Envoi Telegram...")
    send_telegram(message)

    print("\n✅ Agent terminé !")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        erreur = traceback.format_exc()
        print(f"\n❌ ERREUR FATALE : {e}")
        print(erreur)
        try:
            send_telegram(
                f"⚠️ ERREUR — Agent pronostiqueur\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le script a planté :\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Vérifie les logs sur GitHub Actions."
            )
        except Exception:
            print("❌ Impossible d'envoyer l'alerte Telegram.")
        raise
    
