"""Signature des versions du Facturier — à garder sur le PC d'Antoine, jamais publié.

  python signer_version.py nouvelle-cle
      crée cle_privee_facturier.txt (si absente) et affiche la clé publique à mettre dans facturier.pyw
  python signer_version.py signer <archive.zip> <version> <notes.txt> <url>
      écrit version.json (manifeste signé) à côté de l'archive

La clé privée ne quitte jamais ce dossier : sans elle, personne ne peut produire une version
que le Facturier acceptera d'installer, même en prenant le contrôle du compte GitHub.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import ed25519

ICI = Path(__file__).resolve().parent
CLE = ICI / "cle_privee_facturier.txt"


def lire_cle():
    return bytes.fromhex(CLE.read_text(encoding="utf-8").split("=", 1)[-1].strip())


def nouvelle_cle():
    if not CLE.exists():
        CLE.write_text("CLE_PRIVEE=" + os.urandom(32).hex() + "\n", encoding="utf-8")
        print("Clé privée créée :", CLE.name)
    else:
        print("Clé privée existante conservée :", CLE.name)
    print("CLE_PUBLIQUE=" + ed25519.cle_publique(lire_cle()).hex())


def canonique(contenu):
    return json.dumps(contenu, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signer(archive, version, notes, url):
    donnees = Path(archive).read_bytes()
    contenu = {"app": "facturier", "version": version, "notes": Path(notes).read_text(encoding="utf-8").strip(),
               "url": url, "sha256": hashlib.sha256(donnees).hexdigest(), "taille": len(donnees)}
    sig = ed25519.signer(lire_cle(), canonique(contenu))
    sortie = Path(archive).with_name("version.json")
    sortie.write_text(json.dumps({"contenu": contenu, "signature": sig.hex()}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Manifeste signé :", sortie)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "nouvelle-cle":
        nouvelle_cle()
    elif len(sys.argv) == 6 and sys.argv[1] == "signer":
        signer(*sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(1)
