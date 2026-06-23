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
    from config import ODDS_API_KEY, GH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    GH_REPO = os.environ.get("GH_REPO", "")
except ImportError:
    ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
    GH_REPO = os.environ.get("GH_REPO", "")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TZ_TUNIS = timezone(timedelta(hours=1))
ODDS_API_URL = "https://api.the-odds-api.com/v4"

# Mapping compétitions → sport_key The Odds API (même source que les cotes,
# donc même quota, même clé, et couverture garantie de la saison en cours)
COMPETITION_SPORT_KEYS = {
    "FIFA World Cup": "soccer_fifa_world_cup",
    "Copa Libertadores": "soccer_conmebol_libertadores",
    "Copa Sudamericana": "soccer_conmebol_sudamericana",
    "WNBA": "basketball_wnba",
    "MLB": "baseball_mlb",
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

def get_scores(sport_key, days_from=3):
    """Récupère les scores des matchs récents via The Odds API — même source
    que les cotes, donc couverture garantie de la saison/édition en cours
    (contrairement à API-Football dont le plan gratuit est limité à 2022-2024)."""
    r = requests.get(
        f"{ODDS_API_URL}/sports/{sport_key}/scores/",
        params={
            "apiKey": ODDS_API_KEY,
            "daysFrom": days_from,
        },
        timeout=10,
    )
    if r.status_code == 200:
        matches = r.json()
        return [m for m in matches if m.get("completed") is True]
    print(f"   ⚠️  The Odds API erreur {r.status_code} pour {sport_key} — {r.text[:150]}")
    return []


def format_heure_api(commence_time: str) -> str:
    """Reformate commence_time (ISO, UTC) au même format que le champ 'heure'
    des pronostics ('DD/MM HH:MM' en heure tunisienne), pour pouvoir comparer
    si un score retourné par l'API correspond bien à LA bonne occurrence du
    match — indispensable pour des sports comme le MLB où les mêmes équipes
    se rejouent plusieurs jours d'affilée (séries de 3-4 matchs)."""
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        dt_local = dt.astimezone(TZ_TUNIS)
        return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return ""


def check_result(pari, matches):
    """Vérifie si un pronostic est gagné ou perdu à partir des scores The Odds API.
    Format de match attendu : home_team, away_team, scores=[{"name":..., "score":"X"}].
    IMPORTANT : on exige une correspondance de date en plus des noms d'équipes —
    sans ça, un autre match entre les deux mêmes équipes (fréquent en MLB) peut
    être confondu avec celui du pronostic et donner un résultat faux."""
    match_name = pari.get("match", "")
    selection = pari.get("selection", "")
    pari_heure = pari.get("heure", "")

    for m in matches:
        home = m.get("home_team", "")
        away = m.get("away_team", "")

        if not (home.lower() in match_name.lower() or away.lower() in match_name.lower()):
            continue

        # Vérifie que c'est bien LA bonne occurrence du match (même date/heure),
        # pas une autre rencontre entre les mêmes équipes un autre jour
        match_heure = format_heure_api(m.get("commence_time", ""))
        if pari_heure and match_heure and pari_heure != match_heure:
            continue

        scores = m.get("scores")
        if not scores:
            continue

        score_map = {s["name"]: s["score"] for s in scores}
        if home not in score_map or away not in score_map:
            continue
        try:
            score_home = int(score_map[home])
            score_away = int(score_map[away])
        except (TypeError, ValueError):
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
# REGROUPEMENT PAR SESSION (matin/soir)
# ─────────────────────────────────────────

def grouper_par_session(pronostics_resolus, pronostics_en_attente):
    """Regroupe les pronostics (résolus + en attente) par session_id
    (ex: '2026-06-22_matin'), pour pouvoir attendre que toute une session
    soit résolue avant d'envoyer un récap groupé sur Telegram."""
    sessions = {}
    for p in pronostics_resolus:
        sid = p.get("session_id", "inconnue")
        sessions.setdefault(sid, {"resolus": [], "en_attente": []})
        sessions[sid]["resolus"].append(p)
    for p in pronostics_en_attente:
        sid = p.get("session_id", "inconnue")
        sessions.setdefault(sid, {"resolus": [], "en_attente": []})
        sessions[sid]["en_attente"].append(p)
    return sessions


def session_prete_pour_recap(session_data, delai_max_heures=24):
    """Une session est prête pour un récap groupé si tous ses pronostics
    sont résolus, OU si le délai de sécurité (24h par défaut) est dépassé
    depuis la génération — pour ne jamais bloquer indéfiniment un récap
    à cause d'un seul pronostic non vérifiable (sport non couvert, bug API...)."""
    if not session_data["en_attente"]:
        return True, "complet"

    maintenant = datetime.now(TZ_TUNIS)
    for p in session_data["en_attente"]:
        date_generation = p.get("date_generation")
        if not date_generation:
            continue
        try:
            dt = datetime.fromisoformat(date_generation)
            if (maintenant - dt) > timedelta(hours=delai_max_heures):
                return True, "delai_depasse"
        except Exception:
            continue

    return False, "en_cours"


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    print("=" * 50)
    print("🔍 VÉRIFICATEUR DE RÉSULTATS")
    print(f"📅 {datetime.now(TZ_TUNIS).strftime('%d/%m/%Y %H:%M')}")
    print("=" * 50)

    historique, histo_sha = github_get("historique.json")
    if not historique:
        historique = {"paris": [], "stats": {}, "strategie": {}}

    # ── File d'attente persistante ───────────────────────────────
    # paris.json est ÉCRASÉ à chaque lancement de agent.py (4x/jour),
    # donc on ne peut pas s'y fier seul pour retrouver d'anciens pronostics
    # pas encore joués. On maintient ici une liste à part qui survit
    # entre les exécutions, peuplée à partir de paris.json à chaque run.
    attente, attente_sha = github_get("pronostics_en_attente.json")
    if not attente:
        attente = {"paris": []}

    # Récupère les derniers pronostics générés et les ajoute à la file
    # d'attente s'ils n'y sont pas déjà (et pas déjà dans l'historique).
    # Déduplication sur match + heure + sélection : si agent.py est relancé
    # plusieurs fois le même jour (manuellement ou par erreur), on évite
    # d'empiler plusieurs fois le même pronostic exact dans la file d'attente
    # ou l'historique, ce qui fausserait les statistiques d'apprentissage.
    def meme_pronostic(a, b):
        return (
            a.get("match") == b.get("match")
            and a.get("heure") == b.get("heure")
            and a.get("selection") == b.get("selection")
        )

    paris_data, _ = github_get("paris.json")
    doublons_ignores = 0
    if paris_data:
        nouveaux = paris_data.get("paris", [])
        for p in nouveaux:
            deja_attente = any(meme_pronostic(a, p) for a in attente["paris"])
            deja_histo = any(meme_pronostic(h, p) for h in historique["paris"])
            if not deja_attente and not deja_histo:
                attente["paris"].append(p)
            else:
                doublons_ignores += 1

    if doublons_ignores:
        print(f"   ℹ️  {doublons_ignores} pronostic(s) déjà connu(s), ignoré(s) pour éviter les doublons")

    if not attente["paris"]:
        print("⚠️  Aucun pronostic en attente à vérifier")
        return

    print(f"\n📋 {len(attente['paris'])} pronostics en attente de résultat...")

    scores_cache = {}
    nouveaux_resultats = 0
    toujours_en_attente = []

    for pari in attente["paris"]:
        comp = pari.get("competition", "")
        sport_key = next((v for k, v in COMPETITION_SPORT_KEYS.items() if k.lower() in comp.lower()), None)

        if sport_key is None:
            print(f"   ⚠️  {pari.get('match')} — compétition non vérifiable automatiquement ({comp})")
            toujours_en_attente.append(pari)
            continue

        if sport_key not in scores_cache:
            scores_cache[sport_key] = get_scores(sport_key)

        resultat = check_result(pari, scores_cache[sport_key])

        if resultat is None:
            print(f"   ⏳ {pari.get('match')} — match pas encore terminé ou pas trouvé")
            toujours_en_attente.append(pari)
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

    # Met à jour la file d'attente : ne garde que ceux pas encore résolus
    attente["paris"] = toujours_en_attente
    github_save("pronostics_en_attente.json", attente, attente_sha, f"⏳ {len(toujours_en_attente)} pronostics en attente")

    patterns = {}

    if nouveaux_resultats == 0:
        print("\n⏳ Aucun nouveau résultat disponible pour l'instant.")
        # IMPORTANT : on ne s'arrête PAS ici — même sans nouveau résultat ce
        # run-ci, une session déjà partiellement résolue peut avoir dépassé
        # le délai de sécurité de 24h et doit quand même être annoncée.
    else:
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

    # Envoi récap par session (matin/soir), pas au fil de l'eau :
    # on attend que TOUS les pronostics d'une même session soient résolus,
    # sauf si le délai de sécurité de 24h est dépassé pour l'un d'eux.
    # Cette vérification a lieu À CHAQUE run, qu'il y ait eu un nouveau
    # résultat ou non, pour que le délai de 24h puisse toujours se déclencher.
    sessions = grouper_par_session(historique["paris"], attente["paris"])

    # Marqueur pour ne pas renvoyer deux fois le récap d'une session déjà annoncée
    sessions_envoyees = historique.get("sessions_envoyees")
    if sessions_envoyees is None:
        # Première exécution depuis la mise en place du regroupement par
        # session : tout pronostic legacy sans session_id ("inconnue") a déjà
        # été annoncé individuellement par le passé, donc on ne le réannonce pas.
        sessions_envoyees = ["inconnue"]

    au_moins_un_envoi = False
    for session_id, session_data in sessions.items():
        if session_id in sessions_envoyees:
            continue  # déjà annoncée précédemment, on ne répète pas

        pret, raison = session_prete_pour_recap(session_data)
        if not pret:
            continue

        if not session_data["resolus"]:
            continue  # rien à annoncer si aucun résultat connu pour cette session

        gagnes_session = len([p for p in session_data["resolus"] if p.get("resultat") == "gagné"])
        total_session = len(session_data["resolus"])

        recap = build_recap(session_data["resolus"], historique.get("stats", {}), patterns)
        if raison == "delai_depasse" and session_data["en_attente"]:
            recap += f"\n\n⏱️ Note : {len(session_data['en_attente'])} pronostic(s) de cette session n'a/ont pas pu être vérifié(s) après 24h (résultat non disponible)."

        send_telegram(recap)
        sessions_envoyees.append(session_id)
        au_moins_un_envoi = True
        print(f"   📤 Récap envoyé pour la session {session_id} ({gagnes_session}/{total_session} gagnés, {raison})")

    historique["sessions_envoyees"] = sessions_envoyees

    if nouveaux_resultats == 0 and au_moins_un_envoi:
        # Cas particulier : pas de nouveau résultat ce run-ci, mais une
        # session a tout de même été libérée par le délai de 24h. Il faut
        # sauvegarder sessions_envoyees maintenant, sinon le même récap
        # serait renvoyé à chaque run suivant.
        github_save("historique.json", historique, histo_sha, "📤 Session libérée par délai de sécurité (24h)")

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
    try:
        main()
    except Exception as e:
        import traceback
        erreur = traceback.format_exc()
        print(f"\n❌ ERREUR FATALE : {e}")
        print(erreur)
        try:
            send_telegram(
                f"⚠️ ERREUR — Vérification résultats\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le script check_results.py a planté :\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Vérifie les logs sur GitHub Actions pour le détail."
            )
        except Exception:
            print("❌ Impossible d'envoyer l'alerte Telegram non plus.")
        raise
