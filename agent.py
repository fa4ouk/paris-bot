"""
🏆 Agent Parieur Pro - Version The Odds API uniquement
Récupère matchs et cotes directement depuis The Odds API
"""

import requests
import json
from datetime import datetime, timedelta
import os
import base64

# Lecture depuis variables d'environnement (GitHub Actions)
# ou depuis config.py en local
try:
    from config import (
        ODDS_API_KEY, GROQ_API_KEY,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        COTE_MIN, COTE_MAX
    )
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")
except ImportError:
    ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    COTE_MIN = 1.4
    COTE_MAX = 2.5
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")

# 🏆 Championnats avec leurs clés The Odds API
SPORTS_ACTIFS = [
    {"key": "soccer_epl", "name": "Premier League"},
    {"key": "soccer_spain_la_liga", "name": "La Liga"},
    {"key": "soccer_france_ligue_one", "name": "Ligue 1"},
    {"key": "soccer_germany_bundesliga", "name": "Bundesliga"},
    {"key": "soccer_italy_serie_a", "name": "Serie A"},
    {"key": "soccer_uefa_champions_league", "name": "Champions League"},
]

BASE_URL_ODDS = "https://api.the-odds-api.com/v4"
MARCHES = "h2h,totals,spreads"


# ─────────────────────────────────────────
# 1. RÉCUPÉRATION DES MATCHS ET COTES
# ─────────────────────────────────────────

def get_matches_and_odds(sport_key: str) -> list:
    """Récupère les matchs avec leurs cotes pour un sport"""
    url = f"{BASE_URL_ODDS}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us",  # Ajout US pour plus de bookmakers
        "markets": MARCHES,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"   ⚠️  {sport_key}: {r.status_code}")
            if r.status_code == 401:
                print("   ❌ Clé API invalide ! Vérifie ODDS_API_KEY")
            return []
    except Exception as e:
        print(f"   ❌ {sport_key}: {e}")
        return []


def collect_matches() -> list:
    """Collecte les matchs du jour avec cotes"""
    all_matches = []
    print("📡 Collecte des matchs du jour via The Odds API...")
    today = datetime.now().strftime("%d/%m/%Y")
    print(f"   📅 Date : {today}")
    print(f"   🏆 Compétitions actives: {len(SPORTS_ACTIFS)}")
    
    for sport in SPORTS_ACTIFS:
        matches = get_matches_and_odds(sport["key"])
        if matches:
            # Ajoute le nom de la compétition
            for m in matches:
                m["_competition_name"] = sport["name"]
                m["_sport_key"] = sport["key"]
            all_matches.extend(matches)
            print(f"   ✅ {sport['name']}: {len(matches)} matchs")
        else:
            print(f"   ⚠️  {sport['name']}: 0 matchs")
    
    print(f"📊 Total : {len(all_matches)} matchs du jour")
    return all_matches


# ─────────────────────────────────────────
# 2. FILTRAGE DES MATCHS DU JOUR UNIQUEMENT
# ─────────────────────────────────────────

def filter_today_matches(matches: list) -> list:
    """Garde uniquement les matchs du jour"""
    tz_tunis = timezone(timedelta(hours=1))
    aujourd_hui = datetime.now(tz_tunis).date()
    filtered = []
    
    for match in matches:
        try:
            commence = match.get("commence_time", "")
            if not commence:
                continue
                
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            dt_local = dt.astimezone(tz_tunis)
            date_match = dt_local.date()
            
            if date_match == aujourd_hui:
                filtered.append(match)
        except Exception:
            continue
    
    return filtered


# ─────────────────────────────────────────
# 3. PRÉPARATION DES DONNÉES POUR L'IA
# ─────────────────────────────────────────

def format_heure(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        tz_tunis = timezone(timedelta(hours=1))
        dt_local = dt.astimezone(tz_tunis)
        return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return iso_str


def prepare_data(matches: list) -> list:
    """Prépare les données avec les meilleures cotes"""
    data = []
    
    for match in matches:
        try:
            home = match.get("home_team", "?")
            away = match.get("away_team", "?")
            competition = match.get("_competition_name", "?")
            heure = format_heure(match.get("commence_time", ""))
            
            # Extrait les meilleures cotes
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
                            if name == home:
                                label = f"Victoire {home}"
                            elif name == away:
                                label = f"Victoire {away}"
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
                        if COTE_MIN <= cote <= COTE_MAX:
                            if uid not in best or cote > best[uid]["cote"]:
                                best[uid] = {
                                    "label": label,
                                    "cote": cote,
                                    "bookmaker": bookie_name,
                                    "market": mkey,
                                }
            
            if best:
                data.append({
                    "match": f"{home} vs {away}",
                    "competition": competition,
                    "heure": heure,
                    "selections": list(best.values()),
                })
                
        except Exception as e:
            print(f"⚠️  Erreur préparation match: {e}")
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

        types = ["ULTRA SAFE", "VALEUR", "OPPORTUNISTE"]
        analyse = {}
        for t in types:
            subset = [p for p in paris if p.get("type") == t]
            gagnes = [p for p in subset if p.get("resultat") == "gagné"]
            taux = round(len(gagnes) / len(subset) * 100) if subset else None
            analyse[t] = {"total": len(subset), "gagnes": len(gagnes), "taux": taux}

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

    compressed = []
    for m in data[:25]:
        compressed.append({
            "m": m["match"],
            "c": m["competition"],
            "h": m["heure"],
            "s": [{"l": s["label"], "c": s["cote"], "b": s["bookmaker"]} for s in m["selections"][:8]],
        })

    summary = json.dumps(compressed, ensure_ascii=False, separators=(",", ":"))

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
    for sport in SPORTS_ACTIFS:
        print(f"   - {sport['name']} ({sport['key']})")
    print("=" * 50)

    # Collecte des matchs avec cotes
    matches = collect_matches()
    if not matches:
        send_telegram("⚠️ Aucun match disponible. Vérifie la clé API.")
        return

    # Filtre matchs du jour
    matches_today = filter_today_matches(matches)
    print(f"\n📅 Après filtrage: {len(matches_today)} matchs du jour")

    if not matches_today:
        send_telegram("⚠️ Aucun match du jour disponible.")
        return

    # Préparation des données
    data = prepare_data(matches_today)
    print(f"📊 {len(data)} matchs avec cotes dans la plage {COTE_MIN}→{COTE_MAX}")

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
