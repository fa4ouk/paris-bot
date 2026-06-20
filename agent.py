"""
🏆 Agent Parieur Pro
Analyse les marchés du jour et génère des paris via Groq AI.
"""

import requests
import json
from datetime import datetime
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

SPORTS_ACTIFS = [
    "soccer_fifa_world_cup",
    "basketball_wnba",
    "baseball_mlb",
    "mma_mixed_martial_arts",
]

# Tous les marchés disponibles
MARCHES = "h2h,totals,spreads"

BASE_URL = "https://api.the-odds-api.com/v4"


# ─────────────────────────────────────────
# 1. RÉCUPÉRATION DES COTES
# ─────────────────────────────────────────

def get_odds(sport_key: str) -> list:
    url = f"{BASE_URL}/sports/{sport_key}/odds"
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
            return r.json()
        print(f"   ⚠️  {sport_key}: {r.status_code}")
        return []
    except Exception as e:
        print(f"   ❌ {sport_key}: {e}")
        return []


def collect_matches() -> list:
    all_matches = []
    print("📡 Collecte des matchs...")
    for sport in SPORTS_ACTIFS:
        matches = get_odds(sport)
        for m in matches:
            m["_sport"] = sport
        if matches:
            all_matches.extend(matches)
            print(f"   ✅ {sport}: {len(matches)} matchs")
    print(f"📊 Total : {len(all_matches)} matchs")
    return all_matches


# ─────────────────────────────────────────
# 2. PRÉPARATION DES DONNÉES
# ─────────────────────────────────────────

def format_heure(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # Tunisie = UTC+1
        from datetime import timezone, timedelta
        tz_tunis = timezone(timedelta(hours=1))
        dt_local = dt.astimezone(tz_tunis)
        return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return iso_str


def prepare_data(matches: list) -> list:
    data = []
    # Filtre les matchs selon l'heure de lancement
    # Matin (avant 15h) → matchs du jour uniquement
    # Soir (après 15h) → matchs du jour + lendemain avant 6h (pour les noctambules)
    from datetime import timezone, timedelta
    tz_tunis = timezone(timedelta(hours=1))
    maintenant = datetime.now(tz_tunis)
    aujourd_hui = maintenant.date()
    est_soir = maintenant.hour >= 15

    for match in matches:
        try:
            commence = match.get("commence_time", "")
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            dt_local = dt.astimezone(tz_tunis)
            date_match = dt_local.date()
            heure_match = dt_local.hour

            if date_match == aujourd_hui:
                pass  # Toujours inclus
            elif est_soir and date_match == aujourd_hui + timedelta(days=1) and heure_match < 6:
                pass  # Inclus si soir et match avant 6h le lendemain
            else:
                continue  # Ignoré
        except Exception:
            continue

        home = match.get("home_team", "?")
        away = match.get("away_team", "?")
        competition = match.get("sport_title", "?")
        heure = format_heure(match.get("commence_time", ""))

        # Collecte toutes les sélections, meilleure cote par sélection
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

    return data


# ─────────────────────────────────────────
# 3. LECTURE DE L'HISTORIQUE
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

        import base64
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

        # Stats globales
        stats = content.get("stats", {})

        return {
            "total_paris": len(paris),
            "taux_global": stats.get("taux", 0),
            "par_type": analyse,
            "par_marche": marches,
            "derniers_paris": paris[:5],  # 5 derniers pour contexte
        }
    except Exception as e:
        print(f"⚠️  Impossible de lire l'historique : {e}")
        return {}


# ─────────────────────────────────────────
# 4. ANALYSE PAR L'IA (Groq)
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
- Si taux < 50% sur un type → sois plus sélectif, exige un niveau de confiance plus élevé
- Si taux > 70% sur un type → tu peux être plus généreux sur le nombre de pronostics de ce type
- Évite les marchés où tu te trompes souvent
- Mentionne dans ta note comment tu adaptes ta stratégie
"""
    else:
        histo_context = "Pas encore d'historique disponible — applique une stratégie prudente par défaut."

    prompt = f"""Tu es un pronostiqueur sportif professionnel, reconnu pour la fiabilité de tes analyses sur le long terme.

{histo_context}

Matchs disponibles aujourd'hui (cotes entre {COTE_MIN} et {COTE_MAX}) :
{summary}

Génère les meilleurs pronostics du jour en JSON uniquement, sans texte autour, sans markdown :

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

RÈGLES STRICTES :
- Génère entre 2 et 5 pronostics maximum selon la qualité du jour
- ULTRA SAFE = probabilité >70%, cote modeste mais très fiable
- VALEUR = cote sous-évaluée par le marché (EV positif)
- OPPORTUNISTE = combiné logique 2-3 sélections max
- Pour les combinés : mets chaque sélection comme un pronostic séparé avec le même type "OPPORTUNISTE" et indique dans la raison que c'est à combiner ensemble
- ev_pct : ton estimation de l'Expected Value en %
- Priorise la Coupe du Monde
- Si la journée est vraiment pauvre, génère seulement 1-2 pronostics ULTRA SAFE
- Réponds UNIQUEMENT avec le JSON, rien d'autre"""

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
# 4. FORMATAGE DU MESSAGE TELEGRAM
# ─────────────────────────────────────────

TYPE_EMOJI = {
    "ULTRA SAFE": "🛡️",
    "VALEUR": "💎",
    "OPPORTUNISTE": "🎯",
}

SPORT_EMOJI = {
    "FIFA World Cup": "🌍",
    "WNBA": "🏀",
    "MLB": "⚾",
    "MMA": "🥊",
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
        emoji_sport = next((v for k, v in SPORT_EMOJI.items() if k.lower() in comp.lower()), "⚽️")

        lines.append(f"{emoji_type} PRONO {i} — {ptype}")
        lines.append(f"📋 {pari.get('match', '?')}")
        lines.append(f"🏆 {comp}")
        lines.append(f"⏰ {pari.get('heure', '?')}")
        lines.append(f"🎲 {pari.get('selection', '?')} ({pari.get('style', '')})")
        lines.append(f"📉 Cote : {pari.get('cote', '?')} sur {pari.get('bookmaker', '?')}")
        lines.append(f"📈 EV estimé : +{pari.get('ev_pct', '?')}%")
        lines.append(f"💡 {pari.get('raison', '')}")
        lines.append("")

    if note:
        lines.append(f"📝 {note}")
    lines.append("")
    lines.append("⚠️ Pronostics fournis à titre informatif. Aucune garantie de résultat.")

    return "\n".join(lines)


# ─────────────────────────────────────────
# 5. PUSH VERS GITHUB (pour le dashboard web)
# ─────────────────────────────────────────

def push_to_github(result: dict):
    """Pousse paris.json sur le repo GitHub pour le dashboard web."""
    if not GH_TOKEN or not GH_REPO:
        print("⚠️  GH_TOKEN ou GH_REPO manquant, skip push GitHub")
        return

    url = f"https://api.github.com/repos/{GH_REPO}/contents/paris.json"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Récupère le SHA du fichier existant si il existe
    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    # Encode le contenu en base64
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
            print(f"❌ Erreur GitHub : {r.status_code} — {r.text[:150]}")
    except Exception as e:
        print(f"❌ Erreur push GitHub : {e}")


# ─────────────────────────────────────────
# 6. ENVOI TELEGRAM
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
                print(f"❌ Telegram : {r.status_code} — {r.text[:100]}")
        except Exception as e:
            print(f"❌ Telegram : {e}")


# ─────────────────────────────────────────
# 6. MAIN
# ─────────────────────────────────────────

def main():
    print("=" * 50)
    print("🏆 AGENT PARIEUR PRO — Démarrage")
    print(f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"🎯 Cotes : {COTE_MIN}→{COTE_MAX}")
    print("=" * 50)

    # Collecte
    matches = collect_matches()
    if not matches:
        send_telegram("⚠️ Aucun match disponible. Vérifie la clé API.")
        return

    # Préparation
    print("\n🔍 Analyse des sélections...")
    data = prepare_data(matches)
    print(f"   {len(data)} matchs avec cotes dans la plage {COTE_MIN}→{COTE_MAX}")

    if not data:
        send_telegram(f"📋 Aucune cote entre {COTE_MIN} et {COTE_MAX} aujourd'hui.")
        return

    # Lecture historique
    print("\n📚 Lecture de l'historique...")
    historique = get_historique()
    if historique.get("total_paris", 0) > 0:
        print(f"   {historique['total_paris']} pronostics en historique | Taux : {historique['taux_global']}%")
    else:
        print("   Pas encore d'historique")

    # Analyse IA
    print("\n🤖 Analyse IA en cours...")
    raw = analyze_with_ai(data, historique)

    if not raw:
        send_telegram("❌ Erreur lors de l'analyse IA.")
        return

    # Parse JSON
    try:
        # Nettoie au cas où l'IA met des backticks
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
    except Exception as e:
        print(f"❌ Erreur parsing JSON : {e}")
        print(f"Réponse brute : {raw[:300]}")
        send_telegram(f"❌ Erreur de parsing. Réponse IA :\n{raw[:500]}")
        return

    # Sauvegarde locale
    with open("paris_du_jour.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("💾 Résultats sauvegardés dans paris_du_jour.json")

    # Push GitHub pour le dashboard web
    print("\n📤 Push vers GitHub...")
    push_to_github(result)

    # Message & envoi
    message = build_message(result)
    print("\n--- APERÇU ---")
    print(message)
    print("\n📱 Envoi Telegram...")
    send_telegram(message)

    print("\n✅ Agent terminé !")


if __name__ == "__main__":
    main()
