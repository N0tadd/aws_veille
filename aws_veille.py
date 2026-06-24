"""
Veille AWS — Notifications Discord
By N0tad
"""

import requests, threading, json, os, sys, time, gc, schedule
from playwright.sync_api import sync_playwright

# ─── CONFIG ───────────────────────────────────────────────────────────────────

KEYWORDS        = ["isolation", "doublage", "plafond", "cloison", "menuiserie"] # A personnaliser
BASE_URL        = "https://www.marches-publics.info"
DISCORD_WEBHOOK = "" # A personnaliser

DIR          = os.path.dirname(os.path.abspath(__file__))
FICHIER_VUS  = os.path.join(DIR, "aws_vus.json")
FICHIER_LOG  = os.path.join(DIR, "aws_veille.log")

# HEADER HTTP

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Content-Type":    "application/x-www-form-urlencoded",
    "Origin":          BASE_URL,
}

# ─── LOGS ─────────────────────────────────────────────────────────────────────

class Logger:
    def __init__(self, filepath):
        self.terminal = sys.stdout
        self.log      = open(filepath, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = Logger(FICHIER_LOG)

# ─── STOCKAGE ─────────────────────────────────────────────────────────────────

def charger_vus() -> set:
    if not os.path.exists(FICHIER_VUS):
        return set()
    try:
        with open(FICHIER_VUS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, ValueError):
        print("[WARN] aws_vus.json corrompu, réinitialisation")
        return set()

def sauvegarder_vus(vus: set):
    with open(FICHIER_VUS, "w", encoding="utf-8") as f:
        json.dump(list(vus), f, indent=2)

# ─── DISCORD ──────────────────────────────────────────────────────────────────

def envoyer_discord(lien: str, infos: dict):
    message = {
        "embeds": [{
            "title":       "📢 Nouvel avis — marches-publics.info",
            "description": f"[Consulter l'avis]({lien})",
            "color":       0x1D6FA5,
            "url":         lien,
            "fields": [
                {"name": "🏢 Acheteur",   "value": infos["acheteur"],    "inline": True},
                {"name": "⏳ Date limite", "value": infos["date_limite"], "inline": True},
            ]
        }]
    }
    try:
        r = requests.post(DISCORD_WEBHOOK, json=message, timeout=10)
        r.raise_for_status()
        print(f"  [DISCORD] Envoyé : {lien}")
    except Exception as e:
        print(f"  [ERREUR] Discord : {e}")

# ─── REQUÊTE ──────────────────────────────────────────────────────────────────

def fetch(kw: str, results: dict):
    try:
        with requests.Session() as s:
            s.headers.update(HEADERS)
            s.headers["Referer"] = BASE_URL
            s.get(f"{BASE_URL}/Annonces/rechercher", timeout=15)
            s.headers["Referer"] = f"{BASE_URL}/Annonces/rechercher"
            r = s.post(f"{BASE_URL}/Annonces/lister", timeout=15, data=(
                f"IDE=EC&IDN=T&IDP=X&annee=X&Rechercher=Rechercher"
                f"&IDR=35%2C37%2C44%2C49%2C53%2C56%2C72%2C79%2C85" # A personnaliser
                f"&txtLibre={kw}&dateParution=+%3D+0" # A personnaliser
                f"&listeCPV=&txtLibreLieuExec=&dateNotifDebut=&dateNotifFin=" # A personnaliser
                f"&txtAcheteurNom=&txtAcheteurSiret=&txtTitulaireNom=&txtTitulaireSiret="
                f"&txtLibreAcheteur=&txtLibreVille=&txtLibreRef=&txtLibreObjet=&dateExpiration="
            ))
            print(f"  [{kw}] {r.status_code} — {len(r.content)} octets")
            results[kw] = r.content.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [ERREUR] fetch({kw}) : {e}")
        results[kw] = ""

# ─── SCRAPE ───────────────────────────────────────────────────────────────────

def scraper():
    try:
        print(f"\n[CHECK] {time.strftime('%H:%M:%S')} — Scraping en cours...")

        # Requêtes en parallèle
        results = {}
        threads = [threading.Thread(target=fetch, args=(kw, results)) for kw in KEYWORDS]
        for t in threads: t.start()
        for t in threads: t.join()

        # Extraction via Playwright
        tous_les_liens = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            for kw in KEYWORDS:
                html = results.get(kw, "")
                if not html:
                    continue
                page = context.new_page()
                page.set_content(html, wait_until="domcontentloaded")
                liens     = page.query_selector_all("a[title='Consulter l\\'avis']")
                acheteurs = page.query_selector_all("h2.h2-avis")
                dates     = page.query_selector_all(".col-6.col-md-6")
                for lien, acheteur, date_limite in zip(liens, acheteurs, dates):
                    href = BASE_URL + lien.get_attribute("href")
                    tous_les_liens[href] = {
                        "acheteur":    acheteur.inner_text().strip(),
                        "date_limite": date_limite.inner_text().strip(),
                    }
                page.close()
            context.close()
            browser.close()

        # Libérer le HTML brut dès que Playwright a fini
        del results
        gc.collect()

        print(f"  {len(tous_les_liens)} lien(s) scrappé(s) au total")

        vus      = charger_vus()
        nouveaux = {l: i for l, i in tous_les_liens.items() if l not in vus}

        if not nouveaux:
            print("  Aucun nouveau lien.")
            return

        print(f"  {len(nouveaux)} nouveau(x) lien(s) trouvé(s) !")
        for lien, infos in sorted(nouveaux.items()):
            envoyer_discord(lien, infos)
            vus.add(lien)
            time.sleep(1)

        sauvegarder_vus(vus)

    except Exception as e:
        print(f"[ERREUR CRITIQUE] scraper() : {e}")
        import traceback; traceback.print_exc()
    finally:
        gc.collect()

# ─── LANCEMENT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Veille marches-publics.info démarrée — toutes les 30 minutes")
    print(f"   Webhook : {DISCORD_WEBHOOK[:50]}...")
    print(f"   Logs    : {FICHIER_LOG}")
    print()

    scraper()

    schedule.every(30).minutes.do(scraper)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"[ERREUR] Boucle principale : {e}")
        time.sleep(30)
