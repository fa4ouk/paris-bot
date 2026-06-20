"""
🔍 Vérificateur automatique de résultats
Tourne après les matchs, vérifie les scores et met à jour l'historique
des pronostics (gagné/perdu). Pas de gestion d'argent — agent pronostiqueur pur.
"""

import requests
import json
import os
import base64
from datetime import datetime, timezone, timedelta

try:
    from config import FOOTBALL_API_KEY, GH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    GH_REPO = os.environ.get("GH_REPO", "")
except ImportError:
    FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TZ_TUNIS = timezone(timedelta(hours=1))
FOOTBALL_API_URL = "https://v3.football.api-sports.io"
SAISON_FOOTBALL = 2026  # Obligatoire avec league= sur API-Football, sinon 0 résultat

# Mapping compétitions → IDs API-Football
COMPETITION_IDS = {
    "FIFA World Cup": 1,
    "Copa Libertadores": 13,
    "Copa Sudamericana": 11,
    "WNBA": None,  # Pas de foot, pas encore vérifiable automatiquement
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

def get_fixtures(competition_id, date_str, season=SAISON_FOOTBALL):
    """Récupère les matchs terminés d'une compétition pour une date donnée.
    IMPORTANT : API-Football exige league + season ensemble, sinon elle
    ne retourne jamais de résultat (c'était le bug qui bloquait tout)."""
    r = requests.get(
        f"{FOOTBALL_API_URL}/fixtures",
        headers={"x-apisports-key": FOOTBALL_API_KEY},
        params={
            "league": competition_id,
            "season": season,
            "date": date_str,
        },
        timeout=10,
    )
    if r.status_code == 200:
        fixtures = r.json().get("response", [])
        return [f for f in fixtures if f.get("fixture", {}).get("status", {}).get("short") == "FT"]
    print(f"   ⚠️  API-Football erreur {r.status_code} pour league={competition_id}")
    return []


def check_result(pari, fixtures):
    """Vérifie si un pronostic est gagné ou perdu. Retourne 'gagné', 'perdu' ou None."""
    match_name = pari.get("match", "")
    selection = pari.get("selection", "")

    for fixture in fixtures:
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        score_home = fixture["goals"]["home"]
        score_away = fixture["goals"]["away"]

        if not (home.lower() in match_name.lower() or away.lower() in match_name.lower()):
            continue
        if score_home is None or score_away is None:
            continue

        if f"Victoire {home}" in selection:
            return "gagné" if score_home > score_away else "perdu"
        elif f"Victoire {away}" in selection:
            return "gagné" if score_away > score_home else "perdu"
        elif "Nul" in selection or "Draw" in selection:
            return "gagné" if score_home == score_away else "perdu"
        elif "Plus de" in selection:
            try:
                seuil = float(selection.split("Plus de")[1].strip().split()[0])
                return "gagné" if (score_home + score_away) > seuil else "perdu"
            except Exception:
                pass
        elif "Moins de" in selection:
            try:
                seuil = float(selection.split("Moins de")[1].strip().split()[0])
                return "gagné" if (score_home + score_away) < seuil else "perdu"
            except Exception:
                pass

    return None


# ─────────────────────────────────────────
# ANALYSE DES PATTERNS & RÉVISION STRATÉGIE
# ─────────────────────────────────────────

def analyze_patterns(historique):
    """Détecte les échecs répétés par type/marché pour ajuster la stratégie future."""
    paris = historique.get("paris", [])
    if len(paris) < 3:
        return {}

    alertes = []
    types = ["ULTRA SAFE", "VALEUR", "OPPORTUNISTE"]
    for t in types:
        subset = [p for p in paris if p.get("type") == t and p.get("resultat") in ["gagné", "perdu"]]
        if len(subset) >= 2:
            pertes_consecutives = 0
            for p in subset[:5]:
                if p.get("resultat") == "perdu":
                    pertes_consecutives += 1
                else:
                    break
            if pertes_consecutives >= 2:
                alertes.append({
                    "type": t,
                    "pertes_consecutives": pertes_consecutives,
                    "action": "etre_plus_selectif" if pertes_consecutives == 2 else "eviter",
                })

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
        marches.setdefault(m, {"total": 0, "gagnes": 0})
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
# TELEGRAM
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
            print("✅ Message envoyé sur Telegram !")
        else:
            print(f"❌ Telegram : {r.status_code} — {r.text[:150]}")
    except Exception as e:
        print(f"❌ Telegram : {e}")


def build_recap(resultats, stats, patterns):
    """Récap des résultats juste vérifiés."""
    now = datetime.now(TZ_TUNIS).strftime("%d/%m/%Y %H:%M")
    lines = []
    lines.append(f"📊 RÉSULTATS DES PRONOSTICS — {now}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    gagnes = 0
    for r in resultats:
        emoji = "✅" if r["resultat"] == "gagné" else "❌"
        if r["resultat"] == "gagné":
            gagnes += 1
        lines.append(f"{emoji} {r['match']}")
        lines.append(f"   {r['selection']} @ {r['cote']} — {r['resultat'].upper()}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🎯 Bilan : {gagnes}/{len(resultats)} pronostics gagnés")
    lines.append(f"📈 Taux de réussite global : {stats.get('taux', 0)}%")

    alertes = patterns.get("alertes", [])
    if alertes:
        lines.append("")
        lines.append("⚠️ AJUSTEMENT STRATÉGIE :")
        for a in alertes:
            action = "plus sélectif" if a["action"] == "etre_plus_selectif" else "ÉVITÉ"
            lines.append(f"   • {a['type']} : {a['pertes_consecutives']} échecs consécutifs → {action} demain")

    marches_faibles = patterns.get("marches_faibles", [])
    if marches_faibles:
        lines.append("")
        lines.append("📉 MARCHÉS DÉCONSEILLÉS :")
        for m in marches_faibles:
            lines.append(f"   • {m['marche']} : {m['taux']}% de réussite seulement")

    return "\n".join(lines)


def build_bilan_periodique(historique, periode="jour"):
    """Construit un bilan quotidien ou hebdomadaire des performances."""
    paris = historique.get("paris", [])
    now = datetime.now(TZ_TUNIS)

    if periode == "jour":
        cutoff = now - timedelta(days=1)
        titre = "📅 BILAN DU JOUR"
    else:
        cutoff = now - timedelta(days=7)
        titre = "📆 BILAN DE LA SEMAINE"

    recents = []
    for p in paris:
        date_str = p.get("date_resultat")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str)
            if d >= cutoff:
                recents.append(p)
        except Exception:
            continue

    if not recents:
        return None

    total = len(recents)
    gagnes = len([p for p in recents if p.get("resultat") == "gagné"])
    taux = round(gagnes / total * 100) if total else 0

    par_type = {}
    for p in recents:
        t = p.get("type", "?")
        par_type.setdefault(t, {"total": 0, "gagnes": 0})
        par_type[t]["total"] += 1
        if p.get("resultat") == "gagné":
            par_type[t]["gagnes"] += 1

    lines = []
    lines.append(f"{titre} — {now.strftime('%d/%m/%Y')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"🎯 {gagnes}/{total} pronostics gagnés ({taux}%)")
    lines.append("")
    lines.append("Détail par type :")
    for t, s in par_type.items():
        t_taux = round(s["gagnes"] / s["total"] * 100) if s["total"] else 0
        lines.append(f"   • {t} : {s['gagnes']}/{s['total']} ({t_taux}%)")

    stats_globales = historique.get("stats", {})
    lines.append("")
    lines.append(f"📈 Taux de réussite global (tout l'historique) : {stats_globales.get('taux', 0)}%")
    lines.append(f"📊 Total pronostics enregistrés : {stats_globales.get('total', 0)}")

    return "\n".join(lines)


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    print("=" * 50)
    print("🔍 VÉRIFICATEUR DE RÉSULTATS")
    print(f"📅 {datetime.now(TZ_TUNIS).strftime('%d/%m/%Y %H:%M')}")
    print("=" * 50)

    paris_data, _ = github_get("paris.json")
    if not paris_data:
        print("⚠️  Pas de paris.json trouvé")
        return

    paris = paris_data.get("paris", [])
    if not paris:
        print("⚠️  Aucun pronostic à vérifier")
        return

    print(f"\n📋 {len(paris)} pronostics à vérifier...")

    historique, histo_sha = github_get("historique.json")
    if not historique:
        historique = {"paris": [], "stats": {}, "strategie": {}}

    date_today = datetime.now(TZ_TUNIS).strftime("%Y-%m-%d")
    fixtures_cache = {}
    nouveaux_resultats = 0

    for pari in paris:
        deja_dans_histo = any(
            h.get("match") == pari.get("match") and h.get("heure") == pari.get("heure")
            for h in historique["paris"]
        )
        if deja_dans_histo:
            print(f"   ⏭️  {pari.get('match')} — déjà enregistré")
            continue

        comp = pari.get("competition", "")
        comp_id = next((v for k, v in COMPETITION_IDS.items() if k.lower() in comp.lower()), None)

        if comp_id is None:
            print(f"   ⚠️  {pari.get('match')} — compétition non vérifiable automatiquement ({comp})")
            continue

        cache_key = f"{comp_id}_{date_today}"
        if cache_key not in fixtures_cache:
            fixtures_cache[cache_key] = get_fixtures(comp_id, date_today)

        resultat = check_result(pari, fixtures_cache[cache_key])

        if resultat is None:
            print(f"   ⏳ {pari.get('match')} — match pas encore terminé ou pas trouvé")
            continue

        entry = {
            **pari,
            "resultat": resultat,
            "date_resultat": datetime.now(TZ_TUNIS).isoformat(),
            "verifie_auto": True,
        }
        # On enlève les anciens champs liés à l'argent si jamais présents (historique précédent)
        entry.pop("mise_tnd", None)
        entry.pop("gain_tnd", None)

        historique["paris"].insert(0, entry)
        nouveaux_resultats += 1

        emoji = "✅" if resultat == "gagné" else "❌"
        print(f"   {emoji} {pari.get('match')} — {resultat}")

    if nouveaux_resultats == 0:
        print("\n⏳ Aucun nouveau résultat disponible pour l'instant.")
        return

    # Recalcule les stats globales
    total = len(historique["paris"])
    gagnes = len([p for p in historique["paris"] if p.get("resultat") == "gagné"])
    historique["stats"] = {
        "total": total,
        "gagnes": gagnes,
        "perdus": total - gagnes,
        "taux": round(gagnes / total * 100) if total else 0,
        "derniere_maj": datetime.now(TZ_TUNIS).isoformat(),
    }

    # Analyse les patterns
    patterns = analyze_patterns(historique)
    historique["strategie"] = patterns

    if patterns.get("alertes"):
        print("\n⚠️  AJUSTEMENT STRATÉGIE :")
        for alerte in patterns["alertes"]:
            action = "plus sélectif" if alerte["action"] == "etre_plus_selectif" else "ÉVITÉ"
            print(f"   • {alerte['type']} : {alerte['pertes_consecutives']} échecs consécutifs → {action}")

    if patterns.get("marches_faibles"):
        print("\n📉 MARCHÉS FAIBLES :")
        for m in patterns["marches_faibles"]:
            print(f"   • {m['marche']} : seulement {m['taux']}% de réussite")

    # Sauvegarde
    if github_save("historique.json", historique, histo_sha, f"🔍 {nouveaux_resultats} résultats vérifiés auto"):
        print(f"\n✅ Historique mis à jour ({nouveaux_resultats} nouveaux résultats)")
        print(f"📊 Taux global : {historique['stats']['taux']}%")
    else:
        print("\n❌ Erreur lors de la sauvegarde")
        return

    # Envoi récap immédiat
    nouveaux = historique["paris"][:nouveaux_resultats]
    recap = build_recap(nouveaux, historique["stats"], patterns)
    send_telegram(recap)

    # Bilan quotidien (envoyé uniquement lors du dernier check du jour, vers 02h)
    heure_actuelle = datetime.now(TZ_TUNIS).hour
    if heure_actuelle <= 4:  # check de nuit = fin de journée sportive
        bilan_jour = build_bilan_periodique(historique, "jour")
        if bilan_jour:
            send_telegram(bilan_jour)

        # Bilan hebdomadaire le dimanche soir/nuit
        if datetime.now(TZ_TUNIS).weekday() == 6:  # dimanche
            bilan_semaine = build_bilan_periodique(historique, "semaine")
            if bilan_semaine:
                send_telegram(bilan_semaine)


if __name__ == "__main__":
    main()
