"""Signature des versions du Facturier — à garder sur le PC d'Antoine, jamais publié.

  python signer_version.py ou-est-la-cle
      indique où la clé privée est cherchée, et si elle correspond à la clé publique de facturier.pyw
  python signer_version.py ranger-cle
      déplace une ancienne clé (outils\\cle_privee_facturier.txt, dans le clone du dépôt) vers son emplacement
      hors du dépôt
  python signer_version.py nouvelle-cle
      crée la clé privée UNIQUEMENT s'il n'en existe aucune et si facturier.pyw n'a pas encore de clé publique
  python signer_version.py archives <version>
      fabrique versions\\facturier-<version>.zip (mise à jour) et versions\\Facturier-installation-<version>.zip
      à partir du dossier programme, sans attribut « lecture seule », et affiche l'empreinte SHA-256 de l'archive
      d'installation (à communiquer par SMS / téléphone à la personne qui installe)
  python signer_version.py signer <archive.zip> <version> <notes.txt> <url>
      contrôle l'archive puis écrit version.json (manifeste signé, valable un an) à côté de l'archive.
      Sans nouvelle version pendant 11 mois, relancer la même commande sur la dernière archive pour prolonger.

La clé privée vit HORS du dépôt : par défaut %APPDATA%\\Facturier-signature\\cle_privee_facturier.txt
(ou le fichier désigné par la variable FACTURIER_CLE_PRIVEE). Sans elle, personne ne peut produire une version que
le Facturier acceptera d'installer, même en prenant le contrôle du compte GitHub. Si elle est perdue, plus AUCUN
poste n'acceptera de mise à jour automatique : gardez-en deux copies de secours (voir la procédure).
"""
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import ed25519

ICI = Path(__file__).resolve().parent
DEPOT = ICI.parent
ANCIENNE_CLE = ICI / "cle_privee_facturier.txt"          # ancien emplacement, dans le clone : à ne plus utiliser
PROGRAMME = DEPOT / "programme" / "facturier.pyw"
FICHIERS_PROGRAMME = {"facturier.pyw", "facturier.html", "facturier.ico", "facturier.png", "installer.ps1"}
RATTRAPAGE = "facturier.html.nouveau"   # copie exacte de facturier.html, pour les postes bloqués par « lecture seule »
FORMAT_VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}-\d{1,3}")
VALIDITE_JOURS = 365   # un manifeste signé reste valable un an : re-signer (même archive) au moins une fois par an


def emplacement_cle():
    if os.environ.get("FACTURIER_CLE_PRIVEE"):
        return Path(os.environ["FACTURIER_CLE_PRIVEE"]).expanduser()
    base = os.environ.get("APPDATA") or Path.home() / ".config"
    return Path(base) / "Facturier-signature" / "cle_privee_facturier.txt"


def arreter(msg):
    print("\nARRÊT : " + msg)
    sys.exit(2)


def dans_le_depot(p):
    try:
        p.resolve().relative_to(DEPOT)
        return True
    except ValueError:
        return False


def lire_cle():
    cle = emplacement_cle()
    if not cle.exists():
        if ANCIENNE_CLE.exists():
            arreter(f"la clé est encore dans le dossier du dépôt ({ANCIENNE_CLE}).\n"
                    "Lancez d'abord : python signer_version.py ranger-cle")
        arreter(f"clé privée introuvable ({cle}).\nNE LANCEZ PAS « nouvelle-cle » : une nouvelle clé ne serait acceptée "
                "par aucun Facturier installé.\nRemettez en place la copie de secours (clé USB, gestionnaire de mots de "
                "passe) à cet emplacement, ou indiquez-la avec la variable FACTURIER_CLE_PRIVEE.")
    if dans_le_depot(cle):
        arreter(f"la clé ne doit pas se trouver dans le dossier du dépôt ({cle}).")
    brut = cle.read_text(encoding="utf-8").split("=", 1)[-1].strip()
    if not re.fullmatch(r"[0-9a-f]{64}", brut):
        arreter(f"le fichier {cle} ne contient pas une clé valide (64 caractères 0-9 a-f attendus).")
    return bytes.fromhex(brut)


def cle_publique_du_programme():
    m = re.search(r'FACTURIER_CLE_PUBLIQUE", "([0-9a-f]{64})"', PROGRAMME.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def verifier_correspondance(prive):
    attendue = cle_publique_du_programme()
    obtenue = ed25519.cle_publique(prive).hex()
    if attendue and attendue != obtenue:
        arreter(f"cette clé privée ne correspond PAS à la clé publique de facturier.pyw.\n  attendue : {attendue}\n"
                f"  obtenue  : {obtenue}\nUn manifeste signé avec elle serait refusé par tous les postes.")
    return obtenue


def ou_est_la_cle():
    cle = emplacement_cle()
    print("Emplacement de la clé privée :", cle, "(présente)" if cle.exists() else "(ABSENTE)")
    if ANCIENNE_CLE.exists():
        print("Attention : une clé se trouve encore dans le dépôt :", ANCIENNE_CLE, "→ lancez « ranger-cle »")
    if cle.exists():
        print("Clé publique correspondante :", verifier_correspondance(lire_cle()), "(identique à facturier.pyw)")


def ranger_cle():
    cle = emplacement_cle()
    if not ANCIENNE_CLE.exists():
        arreter(f"aucune clé à ranger dans {ICI}.")
    if dans_le_depot(cle):
        arreter(f"l'emplacement choisi ({cle}) est dans le dépôt.")
    contenu = ANCIENNE_CLE.read_text(encoding="utf-8")
    brut = contenu.split("=", 1)[-1].strip()
    if not re.fullmatch(r"[0-9a-f]{64}", brut):
        arreter(f"le fichier {ANCIENNE_CLE} ne contient pas une clé valide.")
    verifier_correspondance(bytes.fromhex(brut))
    if cle.exists():
        if cle.read_text(encoding="utf-8").strip() != contenu.strip():
            arreter(f"une AUTRE clé existe déjà à {cle} : rien n'a été touché, vérifiez laquelle est la bonne "
                    "(« ou-est-la-cle »).")
    else:
        cle.parent.mkdir(parents=True, exist_ok=True)
        cle.write_text(contenu, encoding="utf-8")
    if lire_cle() != bytes.fromhex(brut):
        arreter(f"la copie vers {cle} n'est pas identique : l'ancienne clé est conservée dans le dépôt.")
    ANCIENNE_CLE.unlink()
    print("Clé rangée dans", cle, "— l'ancienne copie dans le dépôt a été effacée.")
    print("Faites maintenant les deux copies de secours (voir la procédure).")


def nouvelle_cle():
    cle = emplacement_cle()
    if cle.exists() or ANCIENNE_CLE.exists():
        arreter(f"une clé existe déjà ({cle if cle.exists() else ANCIENNE_CLE}) : elle est conservée.")
    if cle_publique_du_programme() and "--remplacer-la-cle-et-casser-les-mises-a-jour" not in sys.argv:
        arreter("facturier.pyw contient déjà une clé publique : la clé privée existe donc quelque part.\n"
                "Retrouvez-la (copie de secours) plutôt que d'en créer une autre : avec une nouvelle clé, AUCUN "
                "Facturier installé n'accepterait plus de mise à jour, il faudrait réinstaller chaque poste à la main.")
    if dans_le_depot(cle):
        arreter(f"l'emplacement choisi ({cle}) est dans le dépôt.")
    cle.parent.mkdir(parents=True, exist_ok=True)
    cle.write_text("CLE_PRIVEE=" + os.urandom(32).hex() + "\n", encoding="utf-8")
    print("Clé privée créée :", cle)
    print("CLE_PUBLIQUE=" + ed25519.cle_publique(lire_cle()).hex())
    print("Faites tout de suite les deux copies de secours (voir la procédure).")


def lecture_seule(chemin_zip):
    """Entrées marquées « lecture seule » (bit DOS 0x01, ou droits Unix sans écriture) : l'Explorateur Windows ou un
    autre extracteur les rendrait non remplaçables, et la mise à jour automatique échouerait."""
    with zipfile.ZipFile(chemin_zip) as z:
        return [i.filename for i in z.infolist() if not i.is_dir() and
                (i.external_attr & 0x01 or ((i.external_attr >> 16) and not (i.external_attr >> 16) & 0o200))]


def controler_archive(donnees, version):
    """Refuse une archive qui casserait les postes : fichier inconnu, version différente, attribut lecture seule."""
    import io
    if not FORMAT_VERSION.fullmatch(version):
        arreter(f"numéro de version invalide : {version} (attendu AAAA.MM.JJ-N)")
    ro = lecture_seule(io.BytesIO(donnees))
    if ro:
        arreter(f"fichier(s) en lecture seule dans l'archive : {ro}. Les postes ne pourraient plus se mettre à jour.\n"
                "Refaites les archives avec : python signer_version.py archives <version>")
    with zipfile.ZipFile(io.BytesIO(donnees)) as z:
        noms = z.namelist()
        if len(noms) != len(set(noms)) or not set(noms) <= FICHIERS_PROGRAMME | {RATTRAPAGE} or not {"facturier.pyw", "facturier.html"} <= set(noms):
            arreter(f"contenu inattendu dans l'archive : {sorted(noms)}")
        if RATTRAPAGE in noms and (z.read(RATTRAPAGE) != z.read("facturier.html") or noms[-1] != "facturier.html"
                                   or noms.index("facturier.pyw") > noms.index(RATTRAPAGE)):
            arreter(f"{RATTRAPAGE} doit être une copie exacte de facturier.html, placée après facturier.pyw, "
                    "et facturier.html doit être le dernier fichier de l'archive")
        m = re.search(r'^VERSION = "([^"]+)"', z.read("facturier.pyw").decode("utf-8"), re.M)
        if not m or m.group(1) != version:
            arreter(f"facturier.pyw de l'archive porte la version {m.group(1) if m else '?'} et non {version}")
        if cle_publique_du_programme() and cle_publique_du_programme().encode() not in z.read("facturier.pyw"):
            arreter("le facturier.pyw de l'archive n'a pas la même clé publique que celui du dépôt")


def signer(archive, version, notes, url):
    donnees = Path(archive).read_bytes()
    controler_archive(donnees, version)
    installation = Path(archive).with_name(f"Facturier-installation-{version}.zip")
    if installation.exists() and lecture_seule(installation):
        arreter(f"{installation.name} contient des fichiers en lecture seule : {lecture_seule(installation)}")
    prive = lire_cle()
    verifier_correspondance(prive)
    contenu = {"app": "facturier", "version": version, "notes": Path(notes).read_text(encoding="utf-8").strip(),
               "url": url, "sha256": hashlib.sha256(donnees).hexdigest(), "taille": len(donnees),
               "expire": (date.today() + timedelta(days=VALIDITE_JOURS)).isoformat()}
    sig = ed25519.signer(prive, canonique(contenu))
    assert ed25519.verifier(ed25519.cle_publique(prive), canonique(contenu), sig)
    sortie = Path(archive).with_name("version.json")
    sortie.write_text(json.dumps({"contenu": contenu, "signature": sig.hex()}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Manifeste signé :", sortie)


def _ajouter(z, nom, donnees):
    info = zipfile.ZipInfo(nom, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 0                 # MS-DOS : attributs Windows ordinaires
    info.external_attr = 0x20              # « archive » seulement, jamais « lecture seule »
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, donnees)


def archives(version):
    prog = DEPOT / "programme"
    if not FORMAT_VERSION.fullmatch(version):
        arreter(f"numéro de version invalide : {version} (attendu AAAA.MM.JJ-N)")
    m = re.search(r'^VERSION = "([^"]+)"', (prog / "facturier.pyw").read_text(encoding="utf-8"), re.M)
    if not m or m.group(1) != version:
        arreter(f"programme\\facturier.pyw porte la version {m.group(1) if m else '?'} et non {version}")
    contenu = {n: (prog / n).read_bytes() for n in sorted(FICHIERS_PROGRAMME)}
    sortie = DEPOT / "versions"
    sortie.mkdir(exist_ok=True)
    maj = sortie / f"facturier-{version}.zip"
    # Ordre voulu : facturier.pyw d'abord, la page en dernier, avec une copie de la page à côté. Un poste bloqué
    # par « lecture seule » (ancien programme) remplace tout sauf la page, et la nouvelle version la met en place.
    ordre = ["facturier.pyw", RATTRAPAGE] + [n for n in sorted(contenu) if n not in ("facturier.pyw", "facturier.html")] + ["facturier.html"]
    with zipfile.ZipFile(maj, "w") as z:
        for n in ordre:
            _ajouter(z, n, contenu["facturier.html" if n == RATTRAPAGE else n])
    inst = sortie / f"Facturier-installation-{version}.zip"
    with zipfile.ZipFile(inst, "w") as z:
        for n in ("Installer le Facturier.bat", "LISEZ-MOI.txt"):
            _ajouter(z, "Facturier/" + n, (DEPOT / n).read_bytes())
        for n in sorted(contenu):
            _ajouter(z, "Facturier/programme/" + n, contenu[n])
    controler_archive(maj.read_bytes(), version)
    if lecture_seule(inst):
        arreter("archive d'installation incorrecte")
    print("Archive de mise à jour   :", maj)
    print("Archive d'installation   :", inst)
    print("Empreinte SHA-256 de l'archive d'installation (à dicter / envoyer par SMS à la personne qui installe) :")
    print("   ", hashlib.sha256(inst.read_bytes()).hexdigest().upper())


def canonique(contenu):
    return json.dumps(contenu, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


if __name__ == "__main__":
    commande = sys.argv[1] if len(sys.argv) >= 2 else ""
    if commande == "nouvelle-cle":
        nouvelle_cle()
    elif commande == "ou-est-la-cle":
        ou_est_la_cle()
    elif commande == "ranger-cle":
        ranger_cle()
    elif commande == "archives" and len(sys.argv) == 3:
        archives(sys.argv[2])
    elif commande == "signer" and len(sys.argv) == 6:
        signer(*sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(1)
