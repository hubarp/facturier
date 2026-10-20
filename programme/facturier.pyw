"""Facturier — programme local.

Ouvre le facturier dans sa propre fenêtre (Chrome ou Edge en mode application), sans console,
garde les données dans le dossier « donnees » et transmet les demandes à Super PDP
avec les identifiants, qui restent sur ce PC et ne sont jamais envoyés au navigateur.

Python 3.9 ou plus récent, sans module supplémentaire. Lancé par le raccourci « Facturier »
(créé par « Installer le Facturier.bat »). Reste actif, invisible, jusqu'à la fermeture de la session Windows.
"""
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import subprocess
import uuid
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "2026.10.20-6"
ICI = Path(__file__).resolve().parent                      # dossier « programme »
DONNEES = Path(os.environ.get("FACTURIER_DONNEES") or ICI.parent / "donnees")
PORT = int(os.environ.get("FACTURIER_PORT", "8765"))
API = os.environ.get("SUPER_PDP_API", "https://api.superpdp.tech").rstrip("/")
VIES = os.environ.get("FACTURIER_VIES", "https://ec.europa.eu/taxation_customs/vies/rest-api").rstrip("/")
FICHIER_ENTREPRISES = DONNEES / "entreprises.json"    # entreprises gérées ; la principale vit directement dans « donnees »
FICHIER_CLIENTS = DONNEES / "clients.json"            # fichier client commun à toutes les entreprises
FICHIER_FORMAT = DONNEES / "format.json"              # numéro de la dernière transformation de données appliquée
FICHIER_ARRET = DONNEES / ".programme_actif"
JOURNAL = DONNEES / "journal.txt"
PROFIL_NAVIGATEUR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Facturier" / "fenetre"
ARRET = secrets.token_urlsafe(24)
PRECEDENT = ICI.parent / "programme_precedent"            # version précédente, pour revenir en arrière
DEPOT = os.environ.get("FACTURIER_DEPOT", "hubarp/facturier")
MANIFESTE_URL = os.environ.get("FACTURIER_MANIFESTE", f"https://raw.githubusercontent.com/{DEPOT}/main/version.json")
URL_AUTORISEE = os.environ.get("FACTURIER_URL_MAJ", f"https://raw.githubusercontent.com/{DEPOT}/main/versions/")
# Clé publique de signature : seule une version signée par la clé privée d'Antoine peut être installée
CLE_PUBLIQUE = bytes.fromhex(os.environ.get("FACTURIER_CLE_PUBLIQUE", "f7365461d6fafc0ac4e88533bd8cd2c4680806422f919256f058c9423f627f53"))
JETON = secrets.token_urlsafe(24)  # change à chaque lancement ; seule la page servie ici le connaît
HOTES = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ORIGINES = {f"http://{h}" for h in HOTES}
STATUTS_ECHEC = {"fr:213", "api:invalid", "api:rejected", "fr:501"}


# ---------------------------------------------------------------- outils

def journal(msg):
    """Journal sans aucun secret, pour comprendre un problème après coup."""
    ent = getattr(CTX, "entreprise", None)
    ligne = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {'[' + ent + '] ' if ent and ent != 'principale' else ''}{msg}\n"
    try:
        DONNEES.mkdir(parents=True, exist_ok=True)
        if JOURNAL.exists() and JOURNAL.stat().st_size > 500_000:
            JOURNAL.replace(JOURNAL.with_suffix(".ancien.txt"))
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(ligne)
    except OSError:
        pass


# ---------------------------------------------------------------- entreprises
# Chaque entreprise a ses propres données, sauvegardes et identifiants Super PDP. La principale (la SAS) garde
# le dossier « donnees » ; les autres ont chacune un sous-dossier « donnees/entreprises/<id> ».
CTX = threading.local()   # entreprise de la requête en cours (en-tête X-Entreprise)
VERROU_ENTREPRISES = threading.Lock()


class RegistreIllisible(Exception):
    pass


def lire_entreprises():
    """Liste des entreprises. Fichier absent : seulement la principale. Fichier présent mais illisible : erreur
    (jamais de liste réduite qui ferait « disparaître » les autres entreprises à la prochaine écriture)."""
    liste = []
    if FICHIER_ENTREPRISES.exists():
        try:
            liste = json.loads(FICHIER_ENTREPRISES.read_text(encoding="utf-8-sig"))
            if not isinstance(liste, list):
                raise ValueError("liste attendue")
        except (OSError, ValueError) as e:
            raise RegistreIllisible(f"Liste des entreprises illisible ({FICHIER_ENTREPRISES.name}) : {e}")
        liste = [e for e in liste if isinstance(e, dict) and re.fullmatch(r"[a-z0-9]{1,20}", str(e.get("id", "")))]
    if not any(e["id"] == "principale" for e in liste):
        liste.insert(0, {"id": "principale", "nom": ""})
    return liste


def ecrire_entreprises(liste):
    ecrire_json(FICHIER_ENTREPRISES, liste)


def dossier(ident=None):
    ident = ident or getattr(CTX, "entreprise", None) or "principale"
    return DONNEES if ident == "principale" else DONNEES / "entreprises" / ident


def fichier_donnees(ident=None):
    return dossier(ident) / "facturier.json"


def dossier_sauvegardes(ident=None):
    return dossier(ident) / "sauvegardes"


def fichier_ids():
    return dossier() / "identifiants.txt"


def page_facturier():
    """facturier.html s'il existe, sinon le plus récent des facturier*.html du dossier « programme »."""
    p = ICI / "facturier.html"
    if p.exists():
        return p
    candidats = [f for f in ICI.glob("facturier*.html") if "avant" not in f.name]
    return max(candidats, key=lambda f: f.stat().st_mtime) if candidats else None


def lire_identifiants():
    ids = {}
    if fichier_ids().exists():
        for ligne in fichier_ids().read_text(encoding="utf-8-sig").splitlines():
            if "=" in ligne and not ligne.strip().startswith("#"):
                k, v = ligne.split("=", 1)
                ids[k.strip()] = v.strip()
    cid, sec = ids.get("SUPER_PDP_CLIENT_ID", ""), ids.get("SUPER_PDP_CLIENT_SECRET", "")
    return (cid, sec) if cid and sec else None


def ecrire_identifiants(cid, sec):
    dossier().mkdir(parents=True, exist_ok=True)
    fichier_ids().write_text(
        "# Identifiants de l'application Super PDP (ne pas partager ce fichier)\n"
        f"SUPER_PDP_CLIENT_ID={cid}\nSUPER_PDP_CLIENT_SECRET={sec}\n", encoding="utf-8")


class ErreurAPI(Exception):
    def __init__(self, code, corps):
        self.code, self.corps = code, corps
        super().__init__(f"HTTP {code}")


def requete(methode, url, corps=None, entetes=None, timeout=60):
    req = urllib.request.Request(url, data=corps, method=methode, headers=entetes or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def decode(ct, brut):
    if "json" in (ct or ""):
        try:
            return json.loads(brut.decode("utf-8"))
        except ValueError:
            pass
    return brut.decode("utf-8", "replace")[:2000]


# ---------------------------------------------------------------- Super PDP

class SuperPDP:
    def __init__(self):
        self._jeton, self._expire, self._verrou = None, 0, threading.Lock()

    def jeton(self, ids=None, forcer=False):
        """Jeton d'accès OAuth. Avec `ids`, teste des identifiants pas encore enregistrés (rien n'est gardé en mémoire)."""
        essai = ids is not None
        with self._verrou:
            if not essai and not forcer and self._jeton and time.time() < self._expire - 60:
                return self._jeton
            ids = ids if essai else lire_identifiants()
            if not ids:
                raise ErreurAPI(0, "Identifiants Super PDP non renseignés : voir Paramètres > Connexion Super PDP")
            corps = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": ids[0], "client_secret": ids[1]}).encode()
            code, ct, brut = requete("POST", f"{API}/oauth2/token", corps, {"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
            if code != 200:
                raise ErreurAPI(code, "Identifiants refusés par Super PDP" if code in (400, 401) else decode(ct, brut))
            rep = json.loads(brut)
            if essai:
                return rep["access_token"]
            self._jeton, self._expire = rep["access_token"], time.time() + int(rep.get("expires_in", 1800))
            return self._jeton

    def oublier(self):
        with self._verrou:
            self._jeton, self._expire = None, 0

    def appel(self, methode, chemin, params=None, corps=None, type_contenu=None, accepte=None, jeton=None):
        url = f"{API}/v1.beta/{chemin}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        for essai in (0, 1):
            ent = {"Authorization": f"Bearer {jeton or self.jeton(forcer=essai == 1)}"}
            if type_contenu:
                ent["Content-Type"] = type_contenu
            if accepte:
                ent["Accept"] = accepte
            code, ct, brut = requete(methode, url, corps, ent)
            if code == 401 and essai == 0 and not jeton:
                continue
            return code, ct, brut
        return code, ct, brut

    def json(self, methode, chemin, **kw):
        code, ct, brut = self.appel(methode, chemin, **kw)
        obj = decode(ct, brut)
        if code >= 400:
            raise ErreurAPI(code, obj)
        return obj


class SuperPDPParEntreprise:
    """Un client Super PDP (identifiants et jeton) par entreprise, choisi selon la requête en cours."""
    def __init__(self):
        self._clients, self._verrou = {}, threading.Lock()

    def __getattr__(self, nom):
        ident = getattr(CTX, "entreprise", None) or "principale"
        with self._verrou:
            client = self._clients.setdefault(ident, SuperPDP())
        return getattr(client, nom)


SPDP = SuperPDPParEntreprise()


def multipart(nom_fichier, contenu, type_mime="application/pdf"):
    b = uuid.uuid4().hex
    corps = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{nom_fichier}\"\r\n"
             f"Content-Type: {type_mime}\r\n\r\n").encode() + contenu + f"\r\n--{b}--\r\n".encode()
    return corps, f"multipart/form-data; boundary={b}"


def resume_validation(rapport):
    """Liste lisible des erreurs d'un rapport de validation Super PDP."""
    erreurs = []
    for f in (rapport.get("data") or []) if isinstance(rapport, dict) else []:
        for s in f.get("subreports") or []:
            for e in s.get("failures") or []:
                erreurs.append(e.get("message") or e.get("text") or json.dumps(e, ensure_ascii=False)[:300])
    valide = isinstance(rapport, dict) and all(f.get("is_valid") for f in rapport.get("data") or [{}])
    return valide, erreurs


# ---------------------------------------------------------------- actions

def act_etat(_q, _corps):
    if not lire_identifiants():
        return {"configure": False}
    try:
        ent = SPDP.json("GET", "companies/me")
        sess = SPDP.json("GET", "oauth2_sessions/me")
        return {"configure": True, "entreprise": ent, "adresse": adresse_reception(),
                "verification": sess.get("company_verification_status") if isinstance(sess, dict) else None}
    except ErreurAPI as e:
        return {"configure": True, "erreur": e.corps if isinstance(e.corps, str) else f"Super PDP a répondu HTTP {e.code}"}
    except OSError as e:
        return {"configure": True, "erreur": f"Super PDP injoignable ({e})"}


def adresse_reception():
    """Adresse électronique de l'entreprise dans l'annuaire (sans le préfixe 0225:), reprise comme adresse du vendeur."""
    try:
        rep = SPDP.json("GET", "directory_entries")
    except (ErreurAPI, OSError):
        return ""
    for e in rep.get("data", []) if isinstance(rep, dict) else []:
        ident = str(e.get("identifier") or "")
        if ident.startswith("0225:") and not e.get("is_replyto"):
            return ident[5:]
    return ""


def act_identifiants(_q, corps):
    d = json.loads(corps or b"{}")
    cid, sec = str(d.get("client_id", "")).strip(), str(d.get("client_secret", "")).strip()
    if not cid or not sec:
        raise ErreurAPI(400, "Identifiant et secret obligatoires")
    jeton = SPDP.jeton(ids=(cid, sec))           # vérifie avant d'enregistrer
    ent = SPDP.json("GET", "companies/me", jeton=jeton)
    ecrire_identifiants(cid, sec)
    SPDP.oublier()
    journal("Identifiants Super PDP enregistrés")
    return {"configure": True, "entreprise": ent}


def act_oublier(_q, _corps):
    if fichier_ids().exists():
        ecrire_identifiants("", "")
    SPDP.oublier()
    journal("Identifiants Super PDP effacés")
    return {"configure": False}


def act_annuaire(q, _corps):
    siren = "".join(ch for ch in q.get("siren", [""])[0] if ch.isdigit())
    if len(siren) != 9:
        raise ErreurAPI(400, "SIREN invalide")
    rep = SPDP.json("GET", "french_directory/entries", params={"number": siren})
    entrees = rep.get("data", []) if isinstance(rep, dict) else []
    return {"siren": siren, "entrees": entrees}


def act_valider(q, corps):
    nom = q.get("nom", ["facture.pdf"])[0]
    mp, ct = multipart(nom, corps)
    rapport = SPDP.json("POST", "validation_reports", corps=mp, type_contenu=ct)
    valide, erreurs = resume_validation(rapport)
    return {"valide": valide, "erreurs": erreurs, "rapport": rapport}


def act_envoyer(q, corps):
    numero = q.get("numero", [""])[0].strip()
    if not numero or not corps or not corps.startswith(b"%PDF"):
        raise ErreurAPI(400, "Facture manquante")
    sess = SPDP.json("GET", "oauth2_sessions/me")
    if isinstance(sess, dict) and sess.get("company_verification_status") not in (None, "verified"):
        raise ErreurAPI(409, f"Entreprise pas encore vérifiée par Super PDP (statut : {sess.get('company_verification_status')}) : terminez la vérification avant d'envoyer")
    # Pas de double envoi : une facture déjà déposée avec ce numéro et non rejetée est renvoyée telle quelle
    recentes = SPDP.json("GET", "invoices", params={"direction": "out", "order": "desc", "limit": 200, "expand[]": "events"})
    for fac in recentes.get("data", []) if isinstance(recentes, dict) else []:
        if fac.get("external_id") == numero and not ({e.get("status_code") for e in fac.get("events") or []} & STATUTS_ECHEC):
            journal(f"Envoi {numero} ignoré : déjà déposée (id {fac.get('id')})")
            return {"envoye": True, "deja": True, "facture": fac}
    val = act_valider({"nom": [f"{numero}.pdf"]}, corps)
    if not val["valide"]:
        journal(f"Envoi {numero} refusé par la validation : {len(val['erreurs'])} erreur(s)")
        return {"envoye": False, "erreurs": val["erreurs"]}
    fac = SPDP.json("POST", "invoices", params={"external_id": numero, "processing_rule": "B2B"}, corps=corps, type_contenu="application/pdf")
    journal(f"Facture {numero} déposée (id {fac.get('id') if isinstance(fac, dict) else '?'})")
    return {"envoye": True, "facture": fac}


def act_suivi(q, _corps):
    ids = [i for i in q.get("ids", [""])[0].split(",") if i.strip().isdigit()][:100]
    res = {}
    for i in ids:
        try:
            rep = SPDP.json("GET", "invoice_events", params={"invoice_id": i, "limit": 100})
            res[i] = rep.get("data", []) if isinstance(rep, dict) else []
        except ErreurAPI as e:
            res[i] = {"erreur": f"HTTP {e.code}"}
    return {"suivi": res}


def act_recues(q, _corps):
    params = {"direction": "in", "order": "desc", "limit": 100, "expand[]": ["en_invoice", "events"]}
    if q.get("apres"):
        params["starting_after_id"] = q["apres"][0]
    return SPDP.json("GET", "invoices", params=params)


# ---------------------------------------------------------------- VIES (n° de TVA intracommunautaire)

ERREURS_VIES = {
    "INVALID_INPUT": "Numéro mal formé pour ce pays : vérifiez-le",
    "INVALID_REQUESTER_INFO": "Votre propre n° de TVA (Paramètres) n'est pas reconnu par VIES",
    "MS_UNAVAILABLE": "Le service du pays du client est momentanément indisponible : réessayez plus tard",
    "MS_MAX_CONCURRENT_REQ": "Le service du pays du client est surchargé : réessayez dans quelques minutes",
    "GLOBAL_MAX_CONCURRENT_REQ": "VIES est surchargé : réessayez dans quelques minutes",
    "SERVICE_UNAVAILABLE": "VIES est momentanément indisponible : réessayez plus tard",
    "TIMEOUT": "Le service du pays du client n'a pas répondu à temps : réessayez plus tard",
    "VAT_BLOCKED": "Ce numéro est bloqué par VIES",
    "IP_BLOCKED": "VIES refuse temporairement les demandes de ce PC : réessayez plus tard",
}


def _texte_vies(v):
    v = str(v or "").strip()
    return "" if v in ("---", "--", "-") else v


def _numero_tva(v):
    return re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()


def act_vies(_q, corps):
    """Vérifie un n° de TVA dans VIES. Avec le n° de TVA de l'entreprise, VIES délivre un n° de consultation,
    preuve officielle de la vérification à conserver."""
    try:
        d = json.loads((corps or b"{}").decode("utf-8"))
    except ValueError:
        raise ErreurAPI(400, "Demande illisible")
    num = _numero_tva(d.get("numero"))
    pays = _numero_tva(d.get("pays"))[:2]
    if re.match(r"^[A-Z]{2}", num):
        pays, num = num[:2], num[2:]
    pays = {"GR": "EL"}.get(pays, pays)
    if not re.fullmatch(r"[A-Z]{2}", pays) or not num:
        raise ErreurAPI(400, "N° de TVA incomplet : il doit commencer par le code du pays (ex. DE123456789)")
    demande = {"countryCode": pays, "vatNumber": num}
    dem = _numero_tva(d.get("demandeur"))
    if re.match(r"^[A-Z]{2}[A-Z0-9]+$", dem):
        demande.update(requesterMemberStateCode=dem[:2], requesterNumber=dem[2:])

    def appeler(dm):
        try:
            code, ct, brut = requete("POST", f"{VIES}/check-vat-number", json.dumps(dm).encode("utf-8"),
                                     {"Content-Type": "application/json", "Accept": "application/json"}, timeout=40)
        except OSError as e:
            raise ErreurAPI(502, f"VIES injoignable : vérifiez la connexion internet ({e})")
        rep = decode(ct, brut)
        if not isinstance(rep, dict):
            raise ErreurAPI(502, f"Réponse inattendue de VIES (HTTP {code})")
        err = ((rep.get("errorWrappers") or [{}])[0] or {}).get("error") or (rep.get("userError") if rep.get("userError") not in (None, "VALID", "INVALID") else None)
        if err or code >= 300:
            return None, err or f"HTTP {code}"
        return rep, None

    rep, err = appeler(demande)
    sans_preuve = False
    if err == "INVALID_REQUESTER_INFO" and "requesterNumber" in demande:
        # Le n° de l'entreprise est refusé : on vérifie quand même le client, sans n° de consultation
        rep, err = appeler({"countryCode": pays, "vatNumber": num})
        sans_preuve = True
    if err:
        journal(f"VIES {pays}{num} : erreur {err}")
        raise ErreurAPI(502 if err not in ("INVALID_INPUT", "VAT_BLOCKED") else 400, ERREURS_VIES.get(err, f"VIES a répondu : {err}"))
    valide = bool(rep.get("valid"))
    journal(f"VIES {pays}{num} : {'valide' if valide else 'invalide'}")
    return {"pays": pays, "numero": num, "valide": valide, "nom": _texte_vies(rep.get("name")),
            "adresse": _texte_vies(rep.get("address")), "consultation": "" if sans_preuve else str(rep.get("requestIdentifier") or ""),
            "date": str(rep.get("requestDate") or datetime.now().astimezone().isoformat(timespec="seconds")),
            "demandeurRefuse": sans_preuve}


# ---------------------------------------------------------------- signature (Ed25519, RFC 8032, vérification seule)

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)

def _inv(x):
    return pow(x, _P - 2, _P)

def _xrecover(y):
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    if x % 2:
        x = _P - x
    return x

_BY = 4 * _inv(5) % _P
_BX = _xrecover(_BY)
_B = (_BX, _BY, 1, _BX * _BY % _P)

def _add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)

def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q

def _encode(pt):
    x, y, z, _ = pt
    zi = _inv(z)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")

def _decode(s):
    if len(s) != 32:
        raise ValueError("point invalide")
    y = int.from_bytes(s, "little")
    signe = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        raise ValueError("point invalide")
    x = _xrecover(y)
    if (x & 1) != signe:
        x = _P - x
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _P:
        raise ValueError("point hors de la courbe")
    return (x, y, 1, x * y % _P)

def _egal(p1, p2):
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0

def _h(m):
    return hashlib.sha512(m).digest()

def _secret(cle):
    h = _h(cle)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]

def ed_verifier(cle_pub: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(signature) != 64:
            return False
        a = _decode(cle_pub)
        r = _decode(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        k = int.from_bytes(_h(signature[:32] + cle_pub + message), "little") % _L
        return _egal(_mul(s, _B), _add(r, _mul(k, a)))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- mises à jour

def num_version(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def canonique(contenu):
    return json.dumps(contenu, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def lire_manifeste():
    code, _ct, brut = requete("GET", f"{MANIFESTE_URL}?t={int(time.time())}", timeout=20)
    if code != 200:
        raise ErreurAPI(502, f"Serveur des mises à jour injoignable (HTTP {code})")
    m = json.loads(brut)
    contenu, signature = m.get("contenu") or {}, bytes.fromhex(m.get("signature", ""))
    if not ed_verifier(CLE_PUBLIQUE, canonique(contenu), signature):
        journal("Mise à jour refusée : signature invalide")
        raise ErreurAPI(400, "Version refusée : sa signature n'est pas valide")
    if contenu.get("app") != "facturier" or not str(contenu.get("url", "")).startswith(URL_AUTORISEE):
        raise ErreurAPI(400, "Version refusée : adresse de téléchargement inattendue")
    return contenu


def version_de(dossier):
    try:
        m = re.search(r'^VERSION = "([^"]+)"', (dossier / "facturier.pyw").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def maj_ignoree():
    try:
        return (DONNEES / "maj_ignoree.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def act_maj_etat(_q, _corps):
    res = {"actuelle": VERSION, "precedente": version_de(PRECEDENT), "disponible": None}
    try:
        c = lire_manifeste()
        if num_version(c["version"]) > num_version(VERSION):
            res["disponible"] = {"version": c["version"], "notes": c.get("notes", ""), "ignoree": c["version"] == maj_ignoree()}
    except (ErreurAPI, OSError, ValueError, KeyError) as e:
        res["erreur"] = e.corps if isinstance(e, ErreurAPI) else "Impossible de vérifier les mises à jour (connexion internet ?)"
    return res


def sauvegarder_donnees(nom, ecraser=True, seulement=None):
    """Copie des données de chaque entreprise et du fichier client commun (avant une mise à jour, une
    transformation, un import). ecraser=False : une copie déjà faite sous ce nom n'est jamais remplacée."""
    for e in lire_entreprises():
        if seulement and e["id"] != seulement:
            continue
        f = fichier_donnees(e["id"])
        cible = dossier_sauvegardes(e["id"]) / f"{nom}.json"
        if f.exists() and (ecraser or not cible.exists()):
            dossier_sauvegardes(e["id"]).mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, cible)
    cible_ent = DONNEES / "sauvegardes" / f"entreprises_{nom}.json"
    if FICHIER_ENTREPRISES.exists() and (ecraser or not cible_ent.exists()):
        cible_ent.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FICHIER_ENTREPRISES, cible_ent)
    cible = DONNEES / "sauvegardes" / f"clients_{nom}.json"
    if FICHIER_CLIENTS.exists() and (ecraser or not cible.exists()):
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FICHIER_CLIENTS, cible)


def fichiers_programme(dossier):
    return {f.name: f.read_bytes() for f in dossier.iterdir() if f.is_file() and not f.name.endswith(".tmp")}


def ecrire_programme(fichiers):
    for nom, contenu in fichiers.items():
        tmp = ICI / (nom + ".tmp")
        tmp.write_bytes(contenu)
        os.replace(tmp, ICI / nom)


def installer_programme(fichiers):
    """Remplace les fichiers du programme ; l'ancienne version part dans « programme_precedent »."""
    ancien = fichiers_programme(ICI)
    if PRECEDENT.exists():
        shutil.rmtree(PRECEDENT)
    PRECEDENT.mkdir()
    for nom, contenu in ancien.items():
        (PRECEDENT / nom).write_bytes(contenu)
    try:
        ecrire_programme(fichiers)
    except OSError:
        ecrire_programme(ancien)  # échec en cours de route : on remet tout comme avant
        raise


def redemarrer():
    """Lance la nouvelle version, qui arrête celle-ci (même mécanisme qu'à l'ouverture)."""
    def lancer():
        options = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen([sys.executable, str(ICI / "facturier.pyw"), "--sans-navigateur"], cwd=str(ICI), close_fds=True, **options)
    threading.Timer(0.5, lancer).start()


def act_maj_installer(_q, _corps):
    c = lire_manifeste()
    if num_version(c["version"]) <= num_version(VERSION):
        raise ErreurAPI(400, "Le Facturier est déjà à jour")
    code, _ct, archive = requete("GET", c["url"], timeout=180)
    if code != 200 or len(archive) != c.get("taille") or hashlib.sha256(archive).hexdigest() != c.get("sha256"):
        raise ErreurAPI(400, "Téléchargement incomplet ou altéré : rien n'a été modifié, réessayez plus tard")
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        noms = [n for n in z.namelist() if not n.endswith("/")]
        if any("/" in n or "\\" in n or n.startswith(".") or ".." in n for n in noms) or not {"facturier.pyw", "facturier.html"} <= set(noms):
            raise ErreurAPI(400, "Archive de mise à jour inattendue : rien n'a été modifié")
        fichiers = {n: z.read(n) for n in noms}
    compile(fichiers["facturier.pyw"], "facturier.pyw", "exec")  # refuse un programme illisible avant de toucher à quoi que ce soit
    sauvegarder_donnees(f"avant_maj_{VERSION}_vers_{c['version']}")
    installer_programme(fichiers)
    try:
        (DONNEES / "maj_ignoree.txt").unlink()
    except OSError:
        pass
    journal(f"Mise à jour {VERSION} -> {c['version']} installée")
    redemarrer()
    return {"ok": True, "version": c["version"]}


def act_maj_retour(_q, _corps):
    precedente = version_de(PRECEDENT)
    if not precedente:
        raise ErreurAPI(400, "Aucune version précédente à restaurer")
    sauvegarder_donnees(f"avant_retour_{VERSION}_vers_{precedente}")
    retour = fichiers_programme(PRECEDENT)
    installer_programme(retour)            # la version actuelle devient à son tour la « précédente »
    (DONNEES / "maj_ignoree.txt").write_text(VERSION, encoding="utf-8")
    journal(f"Retour à la version {precedente} (depuis {VERSION})")
    redemarrer()
    return {"ok": True, "version": precedente}


# ---------------------------------------------------------------- fichier client commun
# Chaque fiche client porte un identifiant interne stable (uid). Les fusions se font par uid ; sans uid (fichier
# d'une ancienne version ou sauvegarde importée), une fiche n'est reconnue comme la même que sur des critères sûrs
# (même code et même nom, ou même SIREN). Un code déjà pris par une autre fiche est renuméroté, et les factures
# de l'entreprise concernée suivent (attribution d'après le nom figé dans la facture en cas d'ambiguïté).

VERROU_DONNEES = threading.Lock()


def norm_nom(v):
    import unicodedata
    v = unicodedata.normalize("NFD", str(v or "").lower())
    return re.sub(r"[^a-z0-9]", "", "".join(ch for ch in v if unicodedata.category(ch) != "Mn"))


def ecrire_json(chemin, obj):
    """Écriture sûre : fichier temporaire vidé sur le disque, puis remplacement d'un seul coup."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=1))
        fh.flush()
        os.fsync(fh.fileno())
    for essai in range(5):                      # antivirus / synchronisation qui tiennent le fichier un instant
        try:
            os.replace(tmp, chemin)
            return
        except PermissionError:
            if essai == 4:
                raise
            time.sleep(0.2 * (essai + 1))


def lire_json(chemin, defaut=None):
    try:
        return json.loads(chemin.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return defaut


def lire_json_strict(chemin, quoi):
    try:
        return json.loads(chemin.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ErreurAPI(500, f"Fichier {quoi} illisible ({chemin.name}) : rien ne sera enregistré. Une copie de la veille est dans « sauvegardes ». ({e})")


def empreinte(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def lire_communs():
    """Contenu complet de clients.json (clients, corbeille, empreintes par entreprise). Erreur s'il est illisible."""
    if not FICHIER_CLIENTS.exists():
        # Fichier supprimé par erreur : reconstruit à partir des copies gardées dans le fichier de chaque entreprise
        journal("Fichier client commun absent : reconstruit à partir des fichiers des entreprises")
        journal(f"Reconstruction : {migration_1_clients_communs()}")
    c = lire_json_strict(FICHIER_CLIENTS, "client commun")
    if not isinstance(c, dict) or not isinstance(c.get("clients"), list):
        raise ErreurAPI(500, "Fichier client commun abîmé : rien ne sera enregistré. Une copie de la veille est dans « sauvegardes ».")
    c.setdefault("clientsSupprimes", [])
    c.setdefault("empreintes", {})
    return c


def _siren(c):
    s = re.sub(r"\D", "", str(c.get("siren") or ""))
    return s if len(s) == 9 else ""


def _code(n):
    return int(re.sub(r"\D", "", str(n or "")) or 0)


def meme_client_sans_uid(a, b):
    """Reconnaissance prudente d'une même fiche quand l'une n'a pas d'uid : jamais deux SIREN différents ;
    même code et même nom, ou même SIREN avec même nom et même ville (deux établissements restent distincts)."""
    sa, sb = _siren(a), _siren(b)
    if sa and sb and sa != sb:
        return False
    meme_nom = bool(norm_nom(a.get("nom"))) and norm_nom(a.get("nom")) == norm_nom(b.get("nom"))
    if str(a.get("numero")) == str(b.get("numero")) and meme_nom:
        return True
    return bool(sa) and sa == sb and meme_nom and norm_nom(a.get("ville")) == norm_nom(b.get("ville"))


def integrer_clients(communs, src_actifs, src_supprimes, factures, mode):
    """Intègre les fiches d'une source (entreprise, fichier d'ancienne version, sauvegarde importée, page) dans le
    fichier commun. mode « source » : la fiche envoyée l'emporte (page, ou fichier modifié hors de cette version) ;
    « ajout » : les fiches déjà connues ne sont pas modifiées (migration, import d'une sauvegarde).
    Les factures de la source (listes modifiées sur place) suivent les codes renumérotés. Renvoie le compte rendu."""
    actifs, supprimes = communs["clients"], communs["clientsSupprimes"]
    # Une fiche sans uid n'est comparée qu'aux fiches présentes avant cet appel (jamais à une autre fiche de la même source)
    actifs_avant, supprimes_avant = list(actifs), list(supprimes)
    base = communs.get("_base") or {}            # uid -> empreinte de la fiche telle que la source l'avait reçue
    tous = lambda: actifs + supprimes
    par_uid = {c.get("uid"): c for c in tous() if c.get("uid")}
    pris = {str(c.get("numero")) for c in tous()}
    renum = []                                   # (ancien code, fiche source, nouveau code)
    rapport = {"ajoutes": 0, "modifies": 0, "renumerotes": []}
    vus_src = set()
    for liste, a_la_corbeille in ((src_actifs or [], False), (src_supprimes or [], True)):
        for c in liste:
            if not isinstance(c, dict) or not c.get("numero"):
                continue
            c = dict(c)
            ancien = str(c["numero"])
            cible = par_uid.get(c.get("uid")) if c.get("uid") else None
            if cible is None and not c.get("uid"):
                cible = next((x for x in actifs_avant if x in actifs and meme_client_sans_uid(c, x)), None)
                if cible is None and a_la_corbeille:
                    cible = next((x for x in supprimes_avant if x in supprimes and meme_client_sans_uid(c, x)), None)
            if cible is not None:
                if str(cible["numero"]) != ancien:
                    renum.append((ancien, c, str(cible["numero"])))
                if mode == "source" and cible.get("uid") not in vus_src:
                    inchange = base.get(cible.get("uid")) == empreinte({**c, "uid": cible.get("uid"), "numero": cible["numero"]})
                    if not inchange and empreinte({**c, "uid": cible.get("uid"), "numero": cible["numero"]}) != empreinte(cible):
                        nouvelle = {**c, "uid": cible["uid"], "numero": cible["numero"]}
                        dans_supprimes = cible in supprimes
                        if dans_supprimes == a_la_corbeille:          # même liste : remplacée sur place (ordre stable)
                            liste_c = supprimes if dans_supprimes else actifs
                            liste_c[liste_c.index(cible)] = nouvelle
                        else:
                            (supprimes if dans_supprimes else actifs).remove(cible)
                            (supprimes if a_la_corbeille else actifs).append(nouvelle)
                        par_uid[cible["uid"]] = nouvelle
                        rapport["modifies"] += 1
                vus_src.add(cible.get("uid"))
                continue
            # Fiche inconnue : ajoutée, avec un nouveau code si le sien est déjà pris
            c.setdefault("uid", secrets.token_hex(8))
            if ancien in pris or c["uid"] in par_uid:
                nouveau = f"C{max([_code(n) for n in pris] + [0]) + 1:05d}"
                renum.append((ancien, c, nouveau))
                rapport["renumerotes"].append(f"{ancien}→{nouveau} ({c.get('nom', '')})")
                c["numero"] = nouveau
            if c["uid"] in par_uid:
                c["uid"] = secrets.token_hex(8)
            pris.add(str(c["numero"]))
            (supprimes if a_la_corbeille else actifs).append(c)
            par_uid[c["uid"]] = c
            vus_src.add(c["uid"])
            rapport["ajoutes"] += 1
    # Factures de la source : chaque facture suit sa fiche ; si plusieurs fiches de la source avaient le même code,
    # la facture va à celle dont le nom correspond au client figé dans la facture
    reaffectations = {}
    if renum:
        par_ancien = {}
        for ancien, fiche, nouveau in renum:
            par_ancien.setdefault(ancien, []).append((fiche, nouveau))
        codes_src = {}
        for liste in (src_actifs or [], src_supprimes or []):
            for c in liste:
                if isinstance(c, dict):
                    codes_src[str(c.get("numero"))] = codes_src.get(str(c.get("numero")), 0) + 1
        for f in factures:
            if not isinstance(f, dict) or str(f.get("clientId")) not in par_ancien:
                continue
            options = par_ancien[str(f["clientId"])]
            nom_fige = norm_nom((f.get("client") or {}).get("nom"))
            choix = next((n for fi, n in options if nom_fige and norm_nom(fi.get("nom")) == nom_fige), None)
            if choix is None:
                if codes_src.get(str(f["clientId"]), 0) > len(options):
                    continue                       # une autre fiche de la source garde ce code : la facture reste
                choix = options[0][1]
            if choix != f["clientId"]:
                reaffectations[f.get("numero")] = choix
                f["clientId"] = choix
    rapport["reaffectations"] = reaffectations
    return rapport


def assurer_uid(liste):
    for c in liste:
        if isinstance(c, dict) and not c.get("uid"):
            c["uid"] = secrets.token_hex(8)


def ecrire_communs(communs, ident=None, donnees=None):
    """Écrit clients.json ; avec une entreprise, retient l'empreinte de son fichier et des fiches qu'il contient,
    pour reconnaître plus tard un fichier modifié par une autre version du programme."""
    communs.pop("_base", None)
    if ident is not None and donnees is not None:
        communs["empreintes"][ident] = {"fichier": empreinte(donnees),
                                        "fiches": {c["uid"]: empreinte(c) for c in donnees.get("clients", []) + donnees.get("clientsSupprimes", []) if c.get("uid")}}
    ecrire_json(FICHIER_CLIENTS, communs)


def usage_clients():
    """Pour chaque code client, les entreprises où il a au moins une facture (y compris à la corbeille)."""
    usage = {}
    for e in lire_entreprises():
        d = lire_json(fichier_donnees(e["id"]), None)
        if not isinstance(d, dict):
            continue
        for f in (d.get("factures") or []) + (d.get("facturesSupprimes") or []):
            cid = f.get("clientId") if isinstance(f, dict) else None
            if cid and e["id"] not in usage.setdefault(cid, []):
                usage[cid].append(e["id"])
    return usage


def act_usage_clients(_q, _corps):
    with VERROU_DONNEES:
        return {"usage": usage_clients()}


def verifier_format():
    if format_donnees() < MIGRATIONS[-1][0]:
        raise ErreurAPI(503, "Données pas encore au nouveau format : fermez la fenêtre et rouvrez le Facturier")


def act_lire_donnees(_q, _corps):
    verifier_format()
    with VERROU_DONNEES:
        d = lire_json_strict(fichier_donnees(), "des données") if fichier_donnees().exists() else None
        communs = lire_communs()
    revision = int((d or {}).get("_revision") or 0)
    if d is not None:
        d["clients"], d["clientsSupprimes"] = communs["clients"], communs["clientsSupprimes"]
        d.pop("_revision", None)
    return {"donnees": d, "revision": revision, "clientsCommuns": {"clients": communs["clients"], "clientsSupprimes": communs["clientsSupprimes"]}}


def act_ecrire_donnees(_q, corps):
    d = json.loads(corps or b"null")
    if not isinstance(d, dict) or not isinstance(d.get("factures"), list) or not isinstance(d.get("clients"), list):
        raise ErreurAPI(400, "Données invalides : rien n'a été enregistré")
    verifier_format()
    with VERROU_DONNEES:
        # Écriture sur la version lue seulement : une fenêtre qui n'a pas vu le dernier enregistrement (autre fenêtre,
        # requête en retard) est refusée au lieu d'écraser en silence
        actuel = lire_json_strict(fichier_donnees(), "des données") if fichier_donnees().exists() else {}
        attendu = int((actuel or {}).get("_revision") or 0)
        try:
            recu = int(q_revision(_q))
        except (TypeError, ValueError):
            raise ErreurAPI(400, "Version des données manquante : rechargez la fenêtre")
        if recu != attendu:
            raise ErreurAPI(409, "Les données ont été modifiées par une autre fenêtre du Facturier depuis leur ouverture ici")
        d["_revision"] = attendu + 1
        res = _ecrire_donnees(d)
        res["revision"] = attendu + 1
        return res


def q_revision(q):
    return (q.get("revision") or [None])[0]


def sauvegarde_du_jour(fichier, dossier_sauv, prefixe):
    """Une sauvegarde par jour : copie de l'état de la veille avant la première écriture du jour (60 gardées)."""
    if not fichier.exists():
        return
    dossier_sauv.mkdir(parents=True, exist_ok=True)
    cible = dossier_sauv / f"{prefixe}_{datetime.now():%Y-%m-%d}.json"
    if not cible.exists():
        cible.write_bytes(fichier.read_bytes())
        for vieux in sorted(dossier_sauv.glob(f"{prefixe}_*.json"))[:-60]:
            try:
                vieux.unlink()
            except OSError:
                pass


def _ecrire_donnees(d, mode="source"):
    FICHIER_DONNEES = fichier_donnees()
    communs = lire_communs()
    ident = getattr(CTX, "entreprise", None) or "principale"
    rapport = integrer_clients(communs, d.get("clients") or [], d.get("clientsSupprimes") or [],
                               (d.get("factures") or []) + (d.get("facturesSupprimes") or []), mode)
    sauvegarde_du_jour(FICHIER_DONNEES, dossier_sauvegardes(), "facturier")
    sauvegarde_du_jour(FICHIER_CLIENTS, DONNEES / "sauvegardes", "clients")
    d["clients"], d["clientsSupprimes"] = communs["clients"], communs["clientsSupprimes"]   # copie (retour arrière)
    ecrire_json(FICHIER_DONNEES, d)
    ecrire_communs(communs, ident, d)
    if rapport["renumerotes"] or rapport["reaffectations"]:
        journal(f"Clients : {rapport}")
    # La page reprend la liste commune (fiches ajoutées ailleurs, codes renumérotés) et les factures réaffectées
    return {"ok": True, "clients": communs["clients"], "clientsSupprimes": communs["clientsSupprimes"],
            "reaffectations": rapport["reaffectations"]}


def act_importer(_q, corps):
    """Import d'une sauvegarde dans l'entreprise ouverte : copie de sécurité, contrôle d'identité, fiches clients
    seulement ajoutées (jamais de modification ni de corbeille des fiches existantes), factures réaffectées."""
    d = json.loads(corps or b"null")
    if not isinstance(d, dict) or not isinstance(d.get("factures"), list) or not isinstance(d.get("clients"), list):
        raise ErreurAPI(400, "Fichier de sauvegarde invalide : rien n'a été importé")
    verifier_format()
    with VERROU_DONNEES:
        actuel = lire_json(fichier_donnees(), None)
        siret = lambda x: re.sub(r"\D", "", str(((x or {}).get("entreprise") or {}).get("siret") or ""))[:9]
        if actuel and siret(actuel) and siret(d) and siret(actuel) != siret(d):
            raise ErreurAPI(400, "Cette sauvegarde est celle d'une autre entreprise (SIREN différent) : ouvrez la bonne entreprise avant d'importer")
        sauvegarder_donnees(f"avant_import_{datetime.now():%Y-%m-%d_%H%M%S}", seulement=getattr(CTX, "entreprise", None) or "principale")
        d["_revision"] = int((actuel or {}).get("_revision") or 0) + 1   # les fenêtres ouvertes avant l'import ne peuvent plus écrire
        # En mode « ajout », la corbeille de la sauvegarde ne déplace ni ne modifie aucune fiche existante : elle sert
        # seulement à rattacher ses factures aux bonnes fiches (ajoutées à la corbeille commune si elles sont inconnues)
        res = _ecrire_donnees(d, mode="ajout")
    journal(f"Sauvegarde importée ({len(d['factures'])} factures)")
    return res


# ---------------------------------------------------------------- transformations des données (une par version de format)

def migration_1_clients_communs():
    """Les fiches clients de toutes les entreprises passent dans un fichier commun (fiches de l'entreprise principale
    d'abord). Une fiche n'est fusionnée avec une autre que sur des critères sûrs ; sinon elle est ajoutée, renumérotée
    si son code est pris, et les factures de son entreprise suivent."""
    communs = {"clients": [], "clientsSupprimes": [], "empreintes": {}}
    resume = []
    lus = []
    for e in lire_entreprises():
        f = fichier_donnees(e["id"])
        if not f.exists():
            continue
        d = lire_json(f, None)
        if not isinstance(d, dict):
            raise RuntimeError(f"données illisibles pour l'entreprise {e['id']}")
        lus.append((e["id"], f, d))
    for ident, f, d in lus:
        factures = (d.get("factures") or []) + (d.get("facturesSupprimes") or [])
        r = integrer_clients(communs, d.get("clients") or [], d.get("clientsSupprimes") or [], factures, "ajout")
        if r["renumerotes"] or r["reaffectations"]:
            resume.append(f"{ident} : {len(r['renumerotes'])} fiche(s) renumérotée(s) {r['renumerotes']}, {len(r['reaffectations'])} facture(s) réaffectée(s)")
    # Tout est prêt en mémoire : écriture des fichiers d'entreprise puis du fichier commun (écrit en dernier)
    for ident, f, d in lus:
        d["clients"], d["clientsSupprimes"] = communs["clients"], communs["clientsSupprimes"]
        ecrire_json(f, d)
        communs["empreintes"][ident] = {"fichier": empreinte(d), "fiches": {c["uid"]: empreinte(c) for c in communs["clients"] + communs["clientsSupprimes"]}}
    ecrire_json(FICHIER_CLIENTS, communs)
    return f"{len(communs['clients'])} clients actifs, {len(communs['clientsSupprimes'])} à la corbeille" + (" ; " + " ; ".join(resume) if resume else "")


MIGRATIONS = [(1, "clients communs", migration_1_clients_communs)]


def format_donnees():
    return int((lire_json(FICHIER_FORMAT, {}) or {}).get("format", 0))


def reprendre_fichiers_modifies():
    """Au démarrage : un fichier d'entreprise écrit par une autre version du programme (retour arrière, restauration)
    est réintégré. Seules les fiches qu'il a réellement changées (ou créées) sont reprises."""
    communs = lire_communs()                     # reconstruit le fichier commun s'il a disparu
    change = False
    for e in lire_entreprises():
        f = fichier_donnees(e["id"])
        d = lire_json(f, None) if f.exists() else None
        if not isinstance(d, dict):
            continue
        connu = communs["empreintes"].get(e["id"]) or {}
        if connu.get("fichier") == empreinte(d):
            continue
        sauvegarder_donnees(f"avant_reprise_{datetime.now():%Y-%m-%d_%H%M%S}", seulement=e["id"])
        communs["_base"] = connu.get("fiches") or {}
        factures = (d.get("factures") or []) + (d.get("facturesSupprimes") or [])
        r = integrer_clients(communs, d.get("clients") or [], d.get("clientsSupprimes") or [], factures, "source")
        d["clients"], d["clientsSupprimes"] = communs["clients"], communs["clientsSupprimes"]
        ecrire_json(f, d)
        ecrire_communs(communs, e["id"], d)
        journal(f"Fichier de l'entreprise {e['id']} modifié hors de cette version : réintégré ({r})")
        change = True
    return change


def appliquer_migrations():
    """Au démarrage, avant d'ouvrir la fenêtre : applique dans l'ordre les transformations pas encore faites
    (après une copie de sécurité qui n'est jamais écrasée), puis réintègre les fichiers modifiés par une autre
    version. Renvoie un message d'erreur ou None."""
    actuel = format_donnees()
    for num, nom, fonction in MIGRATIONS:
        if num <= actuel:
            continue
        try:
            sauvegarder_donnees(f"avant_format_{num}", ecraser=False)
            with VERROU_DONNEES:
                resultat = fonction()
            ecrire_json(FICHIER_FORMAT, {"format": num, "le": datetime.now().isoformat(timespec="seconds")})
            journal(f"Données transformées (format {num}, {nom}) : {resultat}")
        except Exception as e:  # noqa: BLE001
            journal(f"Échec de la transformation des données (format {num}, {nom}) : {e!r}")
            return f"Les données n'ont pas pu être mises au nouveau format ({nom}) : {e}"
    try:
        with VERROU_DONNEES:
            reprendre_fichiers_modifies()
    except Exception as e:  # noqa: BLE001
        journal(f"Échec de la réintégration des fichiers : {e!r}")
        return f"Les données n'ont pas pu être relues : {e}"
    return None


def act_entreprises(_q, _corps):
    return {"entreprises": lire_entreprises()}


def act_ajouter_entreprise(_q, corps):
    d = json.loads(corps or b"{}")
    nom = str(d.get("nom", "")).strip()[:120]
    if not nom:
        raise ErreurAPI(400, "Nom de l'entreprise obligatoire")
    with VERROU_ENTREPRISES:
        liste = lire_entreprises()
        n = 2
        while any(e["id"] == f"e{n}" for e in liste) or (DONNEES / "entreprises" / f"e{n}").exists():
            n += 1
        ent = {"id": f"e{n}", "nom": nom}
        (DONNEES / "entreprises" / ent["id"]).mkdir(parents=True, exist_ok=True)
        ecrire_entreprises(liste + [ent])
    journal(f"Entreprise ajoutée : {ent['id']} ({nom})")
    return {"entreprise": ent, "entreprises": liste + [ent]}


def act_nommer_entreprise(_q, corps):
    d = json.loads(corps or b"{}")
    ident, nom = str(d.get("id", "")), str(d.get("nom", "")).strip()[:120]
    with VERROU_ENTREPRISES:
        liste = lire_entreprises()
        if not any(e["id"] == ident for e in liste) or not nom:
            raise ErreurAPI(400, "Entreprise inconnue ou nom vide")
        for e in liste:
            if e["id"] == ident:
                e["nom"] = nom
                if "siren" in d:
                    e["siren"] = re.sub(r"\D", "", str(d.get("siren") or ""))[:9]
                if d.get("manuel"):
                    e["nomManuel"] = True   # nom choisi à la main : n'est plus recopié depuis Paramètres
        ecrire_entreprises(liste)
    return {"entreprises": liste}


def sauvegarde_complete():
    """Archive zip des données de toutes les entreprises, sans les identifiants Super PDP."""
    tampon = io.BytesIO()
    liste = lire_entreprises()
    with VERROU_DONNEES, zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("entreprises.json", json.dumps(liste, ensure_ascii=False, indent=1))
        if FICHIER_CLIENTS.exists():
            z.write(FICHIER_CLIENTS, "clients.json")
        for e in liste:
            f = fichier_donnees(e["id"])
            if f.exists():
                z.write(f, f"{e['id']}/facturier.json")
        z.writestr("LISEZ-MOI.txt", "Sauvegarde complète du Facturier : un dossier par entreprise (principale = la première).\n"
                   "Les clients sont communs à toutes les entreprises (clients.json) ; chaque facturier.json en contient aussi une copie.\n"
                   "Pour restaurer une entreprise : ouvrez-la dans le Facturier, Paramètres > Sauvegarde & Export > Importer,\n"
                   "et choisissez son fichier facturier.json (après avoir extrait cette archive).\n"
                   "Les identifiants Super PDP ne sont pas inclus : ils restent sur le PC.\n")
    journal(f"Sauvegarde complète exportée ({len(liste)} entreprise(s))")
    return tampon.getvalue()


ACTIONS = {
    ("GET", "/api/entreprises"): act_entreprises,
    ("GET", "/api/clients/usage"): act_usage_clients,
    ("POST", "/api/importer"): act_importer,
    ("POST", "/api/entreprises"): act_ajouter_entreprise,
    ("POST", "/api/entreprises/nom"): act_nommer_entreprise,
    ("GET", "/api/maj"): act_maj_etat,
    ("POST", "/api/maj"): act_maj_installer,
    ("POST", "/api/maj/retour"): act_maj_retour,
    ("GET", "/api/donnees"): act_lire_donnees,
    ("PUT", "/api/donnees"): act_ecrire_donnees,
    ("GET", "/api/etat"): act_etat,
    ("POST", "/api/identifiants"): act_identifiants,
    ("DELETE", "/api/identifiants"): act_oublier,
    ("GET", "/api/annuaire"): act_annuaire,
    ("POST", "/api/valider"): act_valider,
    ("POST", "/api/envoyer"): act_envoyer,
    ("GET", "/api/suivi"): act_suivi,
    ("GET", "/api/recues"): act_recues,
    ("POST", "/api/vies"): act_vies,
}


# ---------------------------------------------------------------- serveur web local

class Serveur(ThreadingHTTPServer):
    """À l'arrêt (mise à jour, retour arrière), les requêtes en cours — un enregistrement — se terminent avant la sortie."""
    daemon_threads = False
    block_on_close = True


_VERROU_FICHIER = None


def prendre_verrou(attente=20):
    """Verrou exclusif sur donnees/.verrou_programme, tenu tant que le programme tourne. Pendant une passation de
    version, attend que l'ancien programme ait fini de s'arrêter. Toute erreur autre que « déjà verrouillé » laisse
    démarrer (mieux vaut démarrer sans verrou que pas du tout)."""
    global _VERROU_FICHIER
    import errno
    try:
        DONNEES.mkdir(parents=True, exist_ok=True)
        fh = open(DONNEES / ".verrou_programme", "a+")
    except OSError as e:
        journal(f"Verrou du programme impossible ({e}) : démarrage sans verrou")
        return True
    occupe = {errno.EACCES, errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN), getattr(errno, "EDEADLK", 36), getattr(errno, "EDEADLOCK", 36)}
    fin = time.time() + attente
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _VERROU_FICHIER = fh
            return True
        except OSError as e:
            if e.errno not in occupe:
                journal(f"Verrou du programme impossible ({e}) : démarrage sans verrou")
                return True
            if time.time() > fin:
                fh.close()
                return False
            time.sleep(0.5)
        except Exception as e:  # noqa: BLE001
            journal(f"Verrou du programme impossible ({e!r}) : démarrage sans verrou")
            return True


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "Facturier"

    def log_message(self, *a):
        pass

    def envoyer(self, code, corps, type_contenu="application/json; charset=utf-8", extra=None):
        if isinstance(corps, (dict, list)):
            corps = json.dumps(corps, ensure_ascii=False).encode("utf-8")
        elif isinstance(corps, str):
            corps = corps.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", type_contenu)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corps)

    def autorise(self, api):
        # Seule la page du facturier servie par ce programme peut utiliser le relais
        if self.headers.get("Host") not in HOTES:
            return False
        if api:
            origine = self.headers.get("Origin")
            if origine is not None and origine not in ORIGINES:
                return False
            if not secrets.compare_digest(self.headers.get("X-Facturier", ""), JETON):
                return False
        return True

    def traiter(self, methode):
        u = urllib.parse.urlparse(self.path)
        api = u.path.startswith("/api/")
        if not self.autorise(api):
            return self.envoyer(403, {"erreur": "Accès refusé"})
        if methode == "GET" and u.path in ("/", "/index.html"):
            if self.headers.get("Sec-Fetch-Site", "") == "cross-site" or self.headers.get("Sec-Fetch-Dest", "") in ("iframe", "frame", "embed", "object"):
                return self.envoyer(403, {"erreur": "Accès refusé"})
            return self.servir_page()
        if methode == "GET" and u.path == "/icone.png" and (ICI / "facturier.png").exists():
            return self.envoyer(200, (ICI / "facturier.png").read_bytes(), "image/png")
        if methode == "GET" and u.path == "/version":
            return self.envoyer(200, {"app": "facturier", "version": VERSION})
        if not api and u.path != "/arret":
            return self.envoyer(404, {"erreur": "Introuvable"})
        corps = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if methode in ("POST", "PUT") else b""
        if u.path == "/arret" and methode == "POST":
            # Arrêt demandé par une version plus récente du programme (clé lue dans le dossier des données)
            if secrets.compare_digest(self.headers.get("X-Arret", ""), ARRET):
                self.envoyer(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self.envoyer(403, {"erreur": "Accès refusé"})
        q = urllib.parse.parse_qs(u.query)
        ident = self.headers.get("X-Entreprise", "") or "principale"
        try:
            connue = any(e["id"] == ident for e in lire_entreprises())
        except RegistreIllisible as e:
            journal(str(e))
            connue = ident == "principale"     # la principale reste accessible même si la liste est abîmée
            if not connue or u.path.startswith("/api/entreprises"):
                return self.envoyer(500, {"erreur": f"{e}. Une copie se trouve dans « sauvegardes »."})
        if not connue:
            return self.envoyer(400, {"erreur": "Entreprise inconnue : fermez la fenêtre et rouvrez le Facturier"})
        CTX.entreprise = ident
        try:
            if methode == "GET" and u.path == "/api/sauvegarde-complete":
                return self.envoyer(200, sauvegarde_complete(), "application/zip",
                                    {"Content-Disposition": f'attachment; filename="facturier_sauvegarde_complete_{datetime.now():%Y-%m-%d}.zip"'})
            if methode == "GET" and u.path.startswith("/api/recues/") and u.path.endswith("/pdf"):
                return self.servir_pdf_recu(u.path.split("/")[3])
            action = ACTIONS.get((methode, u.path))
            if not action:
                return self.envoyer(404, {"erreur": "Action inconnue"})
            return self.envoyer(200, action(q, corps))
        except ErreurAPI as e:
            msg = e.corps if isinstance(e.corps, str) else (e.corps.get("message") or e.corps.get("error") if isinstance(e.corps, dict) else None) or f"Super PDP a répondu HTTP {e.code}"
            journal(f"{methode} {u.path} : erreur {e.code} {str(msg)[:200]}")
            return self.envoyer(502 if e.code >= 500 else (e.code if e.code in (403, 404, 409) else 400), {"erreur": msg, "code": e.code, "detail": e.corps if not isinstance(e.corps, str) else None})
        except OSError as e:
            journal(f"{methode} {u.path} : Super PDP injoignable ({e})")
            return self.envoyer(502, {"erreur": f"Super PDP injoignable : vérifiez la connexion internet ({e})"})
        except Exception as e:  # noqa: BLE001 — on renvoie toujours une réponse lisible
            journal(f"{methode} {u.path} : erreur interne {e!r}")
            return self.envoyer(500, {"erreur": f"Erreur interne du relais : {e}"})

    def servir_page(self):
        p = page_facturier()
        if not p:
            return self.envoyer(404, "Aucun fichier facturier*.html dans le dossier du relais.", "text/plain; charset=utf-8")
        html = p.read_text(encoding="utf-8")
        injection = f"<script>window.RELAIS = {json.dumps({'jeton': JETON, 'fichier': p.name})};</script>"
        html = html.replace("</head>", injection + "</head>", 1)
        html = html.replace("<title>", '<link rel="icon" href="/icone.png"><title>', 1)
        return self.envoyer(200, html, "text/html; charset=utf-8")

    def servir_pdf_recu(self, ident):
        if not ident.isdigit():
            return self.envoyer(400, {"erreur": "Numéro de facture invalide"})
        code, ct, brut = SPDP.appel("GET", f"invoices/{ident}", params={"format": "factur-x"}, accepte="application/pdf")
        if code >= 400 or not brut.startswith(b"%PDF"):
            code, ct, brut = SPDP.appel("GET", f"invoices/{ident}/download")
        if code >= 400:
            raise ErreurAPI(code, decode(ct, brut))
        type_c = "application/pdf" if brut.startswith(b"%PDF") else (ct or "application/octet-stream")
        ext = "pdf" if type_c == "application/pdf" else "xml"
        return self.envoyer(200, brut, type_c, {"Content-Disposition": f'inline; filename="facture_recue_{ident}.{ext}"'})

    def do_GET(self):
        self.traiter("GET")

    def do_POST(self):
        self.traiter("POST")

    def do_DELETE(self):
        self.traiter("DELETE")

    def do_PUT(self):
        self.traiter("PUT")


def message(texte):
    """Message à l'écran même sans console (programme lancé par l'icône)."""
    journal(texte.replace("\n", " "))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, texte, "Facturier", 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    print(texte)


def version_active():
    """Version du programme déjà lancé sur ce PC, ou None."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/version", headers={"Host": f"127.0.0.1:{PORT}"}), timeout=2) as r:
            d = json.loads(r.read())
            return d.get("version") if d.get("app") == "facturier" else "?"
    except urllib.error.HTTPError:
        return "?"
    except (OSError, ValueError):
        return None


def arreter_ancien():
    try:
        cle = json.loads(FICHIER_ARRET.read_text(encoding="utf-8"))["cle"]
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/arret", data=b"", method="POST", headers={"X-Arret": cle, "Host": f"127.0.0.1:{PORT}"})
        urllib.request.urlopen(req, timeout=3).read()
    except (OSError, ValueError, KeyError):
        return False
    for _ in range(30):
        if version_active() is None:
            return True
        time.sleep(0.2)
    return False


def navigateur():
    """Chrome, sinon Edge (toujours présent sous Windows) ; None ailleurs."""
    env = os.environ.get
    candidats = [Path(env(v, "")) / s for v, s in [
        ("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"), ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
        ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"), ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
        ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe")] if env(v)]
    return next((c for c in candidats if c.is_file()), None)


def ouvrir_fenetre(adresse):
    exe = navigateur()
    if exe:
        # Fenêtre d'application (sans barre d'adresse ni onglets), profil réservé au facturier
        PROFIL_NAVIGATEUR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([str(exe), f"--app={adresse}", f"--user-data-dir={PROFIL_NAVIGATEUR}", "--no-first-run",
                          "--no-default-browser-check", "--disable-features=Translate", "--window-size=1400,900"], close_fds=True)
    else:
        webbrowser.open(adresse)


def main():
    adresse = f"http://localhost:{PORT}/"
    fenetre = "--sans-navigateur" not in sys.argv
    active = version_active()
    if active == VERSION:
        if fenetre:
            ouvrir_fenetre(adresse)
        return
    if active is not None and not arreter_ancien():
        message("Une ancienne version du Facturier, ou un autre programme, occupe déjà la place.\n"
                "Fermez la fenêtre noire de l'ancien facturier si elle est ouverte, ou redémarrez l'ordinateur, puis rouvrez le Facturier.")
        return
    if not page_facturier():
        message("Fichier facturier.html introuvable dans le dossier « programme ».")
        return
    # Un seul programme à la fois sur ces données (verrou système, libéré automatiquement à la fin du programme)
    if not prendre_verrou():
        if version_active() and fenetre:
            ouvrir_fenetre(adresse)
        elif not version_active():
            message("Le Facturier est déjà en cours de démarrage ou d'arrêt : patientez quelques secondes puis rouvrez-le.")
        return
    try:
        serveur = Serveur(("127.0.0.1", PORT), Gestionnaire)  # uniquement ce PC, jamais le réseau
    except OSError as e:
        message(f"Le Facturier ne peut pas démarrer : {e}")
        return
    DONNEES.mkdir(parents=True, exist_ok=True)
    erreur = appliquer_migrations()
    if erreur:
        serveur.server_close()
        message(erreur + "\n\nVos données n'ont pas été modifiées (copie de sécurité « avant_format » dans le dossier des sauvegardes).\n"
                "Revenez à la version précédente (dossier « programme_precedent ») ou prévenez Antoine.")
        return
    FICHIER_ARRET.write_text(json.dumps({"cle": ARRET, "version": VERSION}), encoding="utf-8")
    journal(f"Démarrage version {VERSION} ({page_facturier().name}, API {API})")
    print(f"Facturier actif à l'adresse {adresse}")
    if fenetre:
        threading.Timer(0.5, lambda: ouvrir_fenetre(adresse)).start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()          # attend la fin des requêtes en cours
        journal("Arrêt")


if __name__ == "__main__":
    main()
