"""
🔍 Vérificateur automatique de résultats
Tourne 3h après les matchs, vérifie les scores et met à jour l'historique.
"""

import requests
import json
import os
import base64
from datetime import datetime, timezone, timedelta

# Config
try:
    from config import FOOTBALL_API_KEY, GH_TOKEN, BANKROLL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    GH_REPO = os.environ.get("GH_REPO", "")
except ImportError:
    FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")
    BANKROLL = float(os.environ.get("BANKROLL", "100"))
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TZ_TUNIS = timezone(timedelta(hours=1))
FOOTBALL_API_URL = "https://v3.football.api-sports.io"

# Mapping compétitions → IDs API-Football
COMPETITION_IDS = {
    "FIFA World Cup": 1,
    "Copa Libertadores": 13,
    "Copa Sudamericana": 11,
    "WNBA": None,  # Pas de foot
    "MLB": None,
}


# ─────────────────────────────────────────
# GITHUB
# ─────────────────────────────────────────

def github_get(filename):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{filename}"
    r = requests.get(url, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }, timeout=10)
    if r.status_code != 200:
        return None, None
    data = r.json()
    content = json.loads(base64.b64decode(data["content"]).decode())
    return content, data["sha"]


def github_save(filename, content, sha, message):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{filename}"
    encoded = base64.b64encode(json.dumps(content, ensure_ascii=False, indent=2).encode()).decode()
    body = {"message": message, "content": encoded}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }, json=body, timeout=15)
    return r.status_code in [200, 201]


# ─────────────────────────────────────────
# API FOOTBALL
# ─────────────────────────────────────────

def get_fixtures(competition_id, date_str):
    """Récupère les matchs d'une compétition pour une date."""
    r = requests.get(
        f"{FOOTBALL_API_URL}/fixtures",
        headers={
            "x-apisports-key": FOOTBALL_API_KEY,
        },
        params={
            "league": competition_id,
            "date": date_str,
            "status": "FT",  # Full Time seulement
        },
        timeout=10,
    )
    if r.status_code == 200:
        return r.json().get("response", [])
    return []


def check_result(pari, fixtures):
    """
    Vérifie si un pari est gagné ou perdu en comparant avec les scores.
    Retourne 'gagné', 'perdu' ou None si pas trouvé.
    """
    match_name = pari.get("match", "")
    selection = pari.get("selection", "")

    # Cherche le match dans les fixtures
    for fixture in fixtures:
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        score_home = fixture["goals"]["home"]
        score_away = fixture["goals"]["away"]

        # Vérifie si c'est le bon match (comparaison flexible)
        if not (home.lower() in match_name.lower() or away.lower() in match_name.lower()):
            continue

        if score_home is None or score_away is None:
            continue

        # Vérifie le résultat selon la sélection
        if f"Victoire {home}" in selection:
            return "gagné" if score_home > score_away else "perdu"
        elif f"Victoire {away}" in selection:
            return "gagné" if score_away > score_home else "perdu"
        elif "Nul" in selection or "Draw" in selection:
            return "gagné" if score_home == score_away else "perdu"
        elif "Plus de" in selection:
            try:
                seuil = float(selection.split("Plus de")[1].strip().split()[0])
                total = score_home + score_away
                return "gagné" if total > seuil else "perdu"
            except Exception:
                pass
        elif "Moins de" in selection:
            try:
                seuil = float(selection.split("Moins de")[1].strip().split()[0])
                total = score_home + score_away
                return "gagné" if total < seuil else "perdu"
            except Exception:
                pass

    return None  # Match pas trouvé


# ─────────────────────────────────────────
# ANALYSE DES PATTERNS & RÉVISION STRATÉGIE
# ─────────────────────────────────────────

def analyze_patterns(historique):
    """
    Analyse les patterns d'échec et retourne des ajustements de stratégie.
    Si 2-3 pertes consécutives sur un type → alerte.
    """
    paris = historique.get("paris", [])
    if len(paris) < 3:
        return {}

    alertes = []

    # Vérifie les pertes consécutives par type
    types = ["ULTRA SAFE", "VALEUR", "OPPORTUNISTE"]
    for t in types:
        subset = [p for p in paris if p.get("type") == t and p.get("resultat") in ["gagné", "perdu"]]
        if len(subset) >= 2:
            # Compte les pertes consécutives récentes
            pertes_consecutives = 0
            for p in subset[:5]:  # 5 derniers
                if p.get("resultat") == "perdu":
                    pertes_consecutives += 1
                else:
                    break
            if pertes_consecutives >= 2:
                alertes.append({
                    "type": t,
                    "pertes_consecutives": pertes_consecutives,
                    "action": "réduire_mise" if pertes_consecutives == 2 else "éviter",
                })

    # Taux par marché
    marches_faibles = []
    marches = {}
    for p in paris:
        sel = p.get("selection", "")
        if "Plus de" in sel or "Moins de" in sel:
            m = "over_under"
        elif "Victoire" in sel:
            m = "victoire"
        elif "Nul" in sel:
            m = "nul"
        else:
            m = "autre"
        if m not in marches:
            marches[m] = {"total": 0, "gagnes": 0}
        marches[m]["total"] += 1
        if p.get("resultat") == "gagné":
            marches[m]["gagnes"] += 1

    for m, stats in marches.items():
        if stats["total"] >= 3:
            taux = stats["gagnes"] / stats["total"] * 100
            if taux < 40:
                marches_faibles.append({"marche": m, "taux": round(taux)})

    return {
        "alertes": alertes,
        "marches_faibles": marches_faibles,
        "derniere_analyse": datetime.now(TZ_TUNIS).isoformat(),
    }


# ─────────────────────────────────────────
# ENVOI TELEGRAM
# ─────────────────────────────────────────

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        }, timeout=10)
        if r.status_code == 200:
            print("✅ Récap envoyé sur Telegram !")
    except Exception as e:
        print(f"❌ Telegram : {e}")


def build_recap(resultats, stats, patterns):
    """Construit le message récap des résultats."""
    now = datetime.now(TZ_TUNIS).strftime("%d/%m/%Y %H:%M")
    lines = []
    lines.append(f"📊 RÉSULTATS DU JOUR — {now}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    bilan = 0
    for r in resultats:
        emoji = "✅" if r["resultat"] == "gagné" else "❌"
        gain = r["gain_tnd"]
        bilan += gain
        lines.append(f"{emoji} {r['match']}")
        lines.append(f"   {r['selection']} @ {r['cote']} — {gain:+.2f} TND")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 Bilan du jour : {bilan:+.2f} TND")
    lines.append(f"📈 Taux global : {stats.get('taux', 0)}%")
    lines.append(f"💵 Profit net total : {stats.get('profit_net', 0):+.2f} TND")

    # Alertes stratégie
    alertes = patterns.get("alertes", [])
    if alertes:
        lines.append("")
        lines.append("⚠️ ALERTES STRATÉGIE :")
        for a in alertes:
            if a["action"] == "réduire_mise":
                lines.append(f"   • {a['type']} : {a['pertes_consecutives']} pertes consécutives → mises réduites demain")
            else:
                lines.append(f"   • {a['type']} : {a['pertes_consecutives']} pertes consécutives → ÉVITÉ demain")

    marches_faibles = patterns.get("marches_faibles", [])
    if marches_faibles:
        lines.append("")
        lines.append("📉 MARCHÉS DÉCONSEILLÉS :")
        for m in marches_faibles:
            lines.append(f"   • {m['marche']} : {m['taux']}% de réussite seulement")

    lines.append("")
    lines.append("⚠️ Joue responsable.")
    return "\n".join(lines)


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    print("=" * 50)
    print("🔍 VÉRIFICATEUR DE RÉSULTATS")
    print(f"📅 {datetime.now(TZ_TUNIS).strftime('%d/%m/%Y %H:%M')}")
    print("=" * 50)

    # 1. Charge les paris du jour
    paris_data, paris_sha = github_get("paris.json")
    if not paris_data:
        print("⚠️  Pas de paris.json trouvé")
        return

    paris = paris_data.get("paris", [])
    if not paris:
        print("⚠️  Aucun pari à vérifier")
        return

    print(f"\n📋 {len(paris)} paris à vérifier...")

    # 2. Charge l'historique
    historique, histo_sha = github_get("historique.json")
    if not historique:
        historique = {"paris": [], "stats": {}, "strategie": {}}

    # 3. Vérifie chaque pari
    date_hier = (datetime.now(TZ_TUNIS) - timedelta(days=0)).strftime("%Y-%m-%d")
    fixtures_cache = {}
    nouveaux_resultats = 0

    for pari in paris:
        # Skip si déjà vérifié
        match_key = f"{pari.get('match')}_{pari.get('heure')}"
        deja_dans_histo = any(
            h.get("match") == pari.get("match") and h.get("heure") == pari.get("heure")
            for h in historique["paris"]
        )
        if deja_dans_histo:
            print(f"   ⏭️  {pari.get('match')} — déjà enregistré")
            continue

        # Trouve l'ID de la compétition
        comp = pari.get("competition", "")
        comp_id = next((v for k, v in COMPETITION_IDS.items() if k.lower() in comp.lower()), None)

        if comp_id is None:
            print(f"   ⚠️  {pari.get('match')} — compétition non supportée ({comp})")
            continue

        # Récupère les fixtures (avec cache)
        cache_key = f"{comp_id}_{date_hier}"
        if cache_key not in fixtures_cache:
            fixtures_cache[cache_key] = get_fixtures(comp_id, date_hier)

        fixtures = fixtures_cache[cache_key]
        resultat = check_result(pari, fixtures)

        if resultat is None:
            print(f"   ⏳ {pari.get('match')} — match pas encore terminé ou pas trouvé")
            continue

        # Calcule le gain/perte
        mise = pari.get("mise_tnd", 0)
        cote = pari.get("cote", 1)
        gain = round(mise * cote - mise, 2) if resultat == "gagné" else -round(mise, 2)

        # Ajoute à l'historique
        entry = {
            **pari,
            "resultat": resultat,
            "gain_tnd": gain,
            "date_resultat": datetime.now(TZ_TUNIS).isoformat(),
            "verifie_auto": True,
        }
        historique["paris"].insert(0, entry)
        nouveaux_resultats += 1

        emoji = "✅" if resultat == "gagné" else "❌"
        print(f"   {emoji} {pari.get('match')} — {resultat} ({gain:+.2f} TND)")

    if nouveaux_resultats == 0:
        print("\n⏳ Aucun nouveau résultat disponible pour l'instant.")
        return

    # 4. Recalcule les stats
    total = len(historique["paris"])
    gagnes = len([p for p in historique["paris"] if p.get("resultat") == "gagné"])
    profit = sum(p.get("gain_tnd", 0) for p in historique["paris"])

    historique["stats"] = {
        "total": total,
        "gagnes": gagnes,
        "perdus": total - gagnes,
        "taux": round(gagnes / total * 100) if total else 0,
        "profit_net": round(profit, 2),
        "derniere_maj": datetime.now(TZ_TUNIS).isoformat(),
    }

    # 5. Analyse les patterns et révise la stratégie
    patterns = analyze_patterns(historique)
    historique["strategie"] = patterns

    if patterns.get("alertes"):
        print("\n⚠️  ALERTES STRATÉGIE :")
        for alerte in patterns["alertes"]:
            action = "Réduire les mises" if alerte["action"] == "réduire_mise" else "ÉVITER ce type"
            print(f"   • {alerte['type']} : {alerte['pertes_consecutives']} pertes consécutives → {action}")

    if patterns.get("marches_faibles"):
        print("\n📉 MARCHÉS FAIBLES :")
        for m in patterns["marches_faibles"]:
            print(f"   • {m['marche']} : seulement {m['taux']}% de réussite")

    # 6. Sauvegarde
    if github_save("historique.json", historique, histo_sha, f"🔍 {nouveaux_resultats} résultats vérifiés auto"):
        print(f"\n✅ Historique mis à jour ({nouveaux_resultats} nouveaux résultats)")
        print(f"📊 Stats : {historique['stats']['taux']}% | Profit : {historique['stats']['profit_net']} TND")
    else:
        print("\n❌ Erreur lors de la sauvegarde")

    # 7. Envoi récap Telegram
    nouveaux = [p for p in historique["paris"][:nouveaux_resultats]]
    recap = build_recap(nouveaux, historique["stats"], patterns)
    send_telegram(recap)


if __name__ == "__main__":
    main()
