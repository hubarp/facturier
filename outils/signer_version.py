"""Signature des versions du Facturier — à garder sur le PC d'Antoine, jamais publié.

À lancer depuis une invite de commandes ouverte dans le dossier du dépôt, par exemple :
  python outils\\signer_version.py archives 2026.10.25-1

  python outils\\signer_version.py ou-est-la-cle
      indique où la clé privée est cherchée, et si elle correspond à la clé publique de facturier.pyw
  python outils\\signer_version.py ranger-cle
      déplace une ancienne clé (outils\\cle_privee_facturier.txt, dans le clone du dépôt) vers son emplacement
      hors du dépôt
  python outils\\signer_version.py nouvelle-cle
      crée la clé privée UNIQUEMENT s'il n'en existe aucune et si facturier.pyw n'a pas encore de clé publique
  python outils\\signer_version.py archives <version>
      fabrique versions\\facturier-<version>.zip (mise à jour) et versions\\Facturier-installation-<version>.zip
      à partir du dossier programme, sans attribut « lecture seule », et affiche l'empreinte SHA-256 de l'archive
      d'installation (à communiquer par SMS / téléphone à la personne qui installe). Refuse d'écraser une archive
      déjà fabriquée avec un autre contenu (une archive publiée ne change jamais : nouveau numéro de version).
  python outils\\signer_version.py signer <version> <notes.txt>
      contrôle versions\\facturier-<version>.zip puis écrit le manifeste signé (valable un an) dans version.json,
      à la racine du dépôt, là où les postes le lisent. L'adresse de téléchargement est déduite du nom de l'archive.
      notes.txt : les nouveautés, en UTF-8 (le Bloc-notes enregistre en UTF-8, avec ou sans BOM : les deux vont).
      Refuse un numéro plus petit que celui déjà publié, et un numéro déjà publié avec une autre archive.
      Sans nouvelle version pendant 11 mois, relancer la même commande (même version) pour prolonger d'un an.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))   # ed25519.py, à côté de ce fichier
import ed25519  # noqa: E402

ICI = Path(__file__).resolve().parent
DEPOT = ICI.parent
ANCIENNE_CLE = ICI / "cle_privee_facturier.txt"          # ancien emplacement, dans le clone : à ne plus utiliser
PROGRAMME = DEPOT / "programme" / "facturier.pyw"
FICHIERS_PROGRAMME = {"facturier.pyw", "facturier.html", "facturier.ico", "facturier.png", "installer.ps1"}
RATTRAPAGE = "facturier.html.nouveau"   # copie exacte de facturier.html, pour les postes bloqués par « lecture seule »
FORMAT_VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}-\d{1,3}")
VALIDITE_JOURS = 365   # un manifeste signé reste valable un an : re-signer (même archive) au moins une fois par an
# Adresse des archives publiées (doit correspondre à URL_AUTORISEE de facturier.pyw). La variable ne sert qu'aux essais.
URL_PUBLICATION = os.environ.get("FACTURIER_URL_PUBLICATION", "https://raw.githubusercontent.com/hubarp/facturier/main/versions/")
MANIFESTE = DEPOT / "version.json"


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


def num_version(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def lire_notes(notes):
    try:
        texte = Path(notes).read_text(encoding="utf-8-sig").strip()       # UTF-8, avec ou sans BOM
    except OSError as e:
        arreter(f"fichier des nouveautés illisible ({notes}) : {e}")
    except UnicodeDecodeError:
        arreter(f"{notes} n'est pas enregistré en UTF-8. Dans le Bloc-notes : Fichier > Enregistrer sous > Codage : UTF-8.")
    if not texte:
        arreter(f"{notes} est vide : écrivez-y les nouveautés de la version.")
    return texte


def signer(version_ou_archive, notes):
    if version_ou_archive.lower().endswith(".zip"):
        archive = Path(version_ou_archive)
        m = re.fullmatch(r"facturier-(.+)\.zip", archive.name)
        if not m:
            arreter(f"nom d'archive inattendu : {archive.name} (attendu facturier-<version>.zip)")
        version = m.group(1)
    else:
        version = version_ou_archive
        archive = DEPOT / "versions" / f"facturier-{version}.zip"
    if not FORMAT_VERSION.fullmatch(version):
        arreter(f"numéro de version invalide : {version} (attendu AAAA.MM.JJ-N)")
    if not archive.exists():
        arreter(f"archive introuvable : {archive}\nFabriquez-la d'abord : python outils\\signer_version.py archives {version}")
    donnees = archive.read_bytes()
    sha = hashlib.sha256(donnees).hexdigest()
    # Ce qui est déjà publié (version.json à la racine) : jamais un numéro plus petit, jamais un autre contenu sous un
    # numéro déjà publié (les postes déjà à ce numéro ne le recevraient jamais)
    try:
        publie = json.loads(MANIFESTE.read_text(encoding="utf-8-sig")).get("contenu") or {}
    except (OSError, ValueError):
        publie = {}
    if publie.get("version"):
        if num_version(version) < num_version(publie["version"]):
            arreter(f"la version {publie['version']} est déjà publiée : on ne publie jamais un numéro plus petit (les postes le "
                    f"refuseraient).\nPour retirer une mauvaise version, republiez l'ancien contenu sous un numéro PLUS GRAND.")
        if publie["version"] == version and publie.get("sha256") != sha:
            arreter(f"la version {version} est déjà publiée avec une autre archive : on ne change jamais une version publiée.\n"
                    "Donnez un nouveau numéro de version (VERSION dans programme\\facturier.pyw), puis refaites les archives.")
    controler_archive(donnees, version)
    installation = archive.with_name(f"Facturier-installation-{version}.zip")
    if installation.exists() and lecture_seule(installation):
        arreter(f"{installation.name} contient des fichiers en lecture seule : {lecture_seule(installation)}")
    prive = lire_cle()
    verifier_correspondance(prive)
    url = URL_PUBLICATION + archive.name
    contenu = {"app": "facturier", "version": version, "notes": lire_notes(notes),
               "url": url, "sha256": sha, "taille": len(donnees),
               "expire": (date.today() + timedelta(days=VALIDITE_JOURS)).isoformat()}
    sig = ed25519.signer(prive, canonique(contenu))
    assert ed25519.verifier(ed25519.cle_publique(prive), canonique(contenu), sig)
    tmp = MANIFESTE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"contenu": contenu, "signature": sig.hex()}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, MANIFESTE)
    print("Manifeste signé :", MANIFESTE)
    print("Adresse de l'archive :", url)
    print("Valable jusqu'au", contenu["expire"], ": publiez ENSEMBLE version.json et", archive.name,
          "(et l'archive d'installation)." if installation.exists() else ".")


def _ajouter(z, nom, donnees):
    info = zipfile.ZipInfo(nom, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 0                 # MS-DOS : attributs Windows ordinaires
    info.external_attr = 0x20              # « archive » seulement, jamais « lecture seule »
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, donnees)


def _zip(fichiers):
    import io
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        for nom, donnees in fichiers:
            _ajouter(z, nom, donnees)
    return tampon.getvalue()


def archives(version):
    import io
    prog = DEPOT / "programme"
    if not FORMAT_VERSION.fullmatch(version):
        arreter(f"numéro de version invalide : {version} (attendu AAAA.MM.JJ-N)")
    m = re.search(r'^VERSION = "([^"]+)"', (prog / "facturier.pyw").read_text(encoding="utf-8"), re.M)
    if not m or m.group(1) != version:
        arreter(f"programme\\facturier.pyw porte la version {m.group(1) if m else '?'} et non {version}")
    contenu = {n: (prog / n).read_bytes() for n in sorted(FICHIERS_PROGRAMME)}
    # Ordre voulu : facturier.pyw d'abord, la page en dernier, avec une copie de la page à côté. Un poste bloqué
    # par « lecture seule » (ancien programme) remplace tout sauf la page, et la nouvelle version la met en place.
    ordre = ["facturier.pyw", RATTRAPAGE] + [n for n in sorted(contenu) if n not in ("facturier.pyw", "facturier.html")] + ["facturier.html"]
    octets_maj = _zip((n, contenu["facturier.html" if n == RATTRAPAGE else n]) for n in ordre)
    octets_inst = _zip([("Facturier/" + n, (DEPOT / n).read_bytes()) for n in ("Installer le Facturier.bat", "LISEZ-MOI.txt")]
                       + [("Facturier/programme/" + n, contenu[n]) for n in sorted(contenu)])
    # Tout est contrôlé en mémoire : sur une erreur, aucun fichier n'est écrit
    controler_archive(octets_maj, version)
    if lecture_seule(io.BytesIO(octets_inst)):
        arreter("archive d'installation incorrecte")
    sortie = DEPOT / "versions"
    maj, inst = sortie / f"facturier-{version}.zip", sortie / f"Facturier-installation-{version}.zip"
    for chemin, octets in ((maj, octets_maj), (inst, octets_inst)):
        if chemin.exists() and chemin.read_bytes() != octets:
            arreter(f"{chemin.name} existe déjà avec un autre contenu : une archive publiée ne change jamais.\n"
                    "Donnez un nouveau numéro de version (VERSION dans programme\\facturier.pyw). Si cette archive n'a jamais "
                    "été publiée (pas encore envoyée sur GitHub), supprimez-la puis relancez la commande.")
    sortie.mkdir(exist_ok=True)
    for chemin, octets in ((maj, octets_maj), (inst, octets_inst)):
        if chemin.exists():
            print("Déjà fabriquée, identique :", chemin.name)
            continue
        tmp = chemin.with_name(chemin.name + ".tmp")
        tmp.write_bytes(octets)
        os.replace(tmp, chemin)
    print("Archive de mise à jour   :", maj)
    print("Archive d'installation   :", inst)
    print("Empreinte SHA-256 de l'archive d'installation (à dicter / envoyer par SMS à la personne qui installe) :")
    print("   ", hashlib.sha256(octets_inst).hexdigest().upper())
    print(f"Étape suivante : python outils\\signer_version.py signer {version} notes.txt")


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
    elif commande == "signer" and len(sys.argv) == 4:
        signer(*sys.argv[2:])
    elif commande == "signer":
        arreter("usage : python outils\\signer_version.py signer <version> <notes.txt>\n"
                "(l'adresse de téléchargement est maintenant déduite du nom de l'archive, et version.json est écrit à la racine)")
    else:
        print(__doc__)
        sys.exit(1)
