# Facturier

Facturier de vigneron : factures, clients, produits, déclaration aux douanes, facture électronique (Factur-X) envoyée via la plateforme agréée Super PDP.

- `programme/` : l'application (page `facturier.html` + programme local `facturier.pyw`, Python standard, sans dépendance).
- `Installer le Facturier.bat` : installation sous Windows (Python si besoin, raccourci « Facturier »).
- `versions/` : archives de chaque version (programme seul, et dossier complet pour une première installation), fabriquées uniquement avec `python outils/signer_version.py archives <version>`.
- `version.json` : manifeste de la dernière version, **signé** (Ed25519) et **daté** (valable un an : le re-signer au moins une fois par an). Le Facturier installé le consulte et n'installe une mise à jour que si la signature correspond à la clé publique intégrée dans `facturier.pyw`.
- `outils/` : fabrication et signature des versions (`signer_version.py`). La clé privée reste sur le poste du mainteneur, **hors du dépôt** (`%APPDATA%\Facturier-signature\`), jamais dans ce dossier.

Aucune donnée de client ni aucun identifiant ne se trouve dans ce dépôt : les données vivent dans le dossier `donnees/` de chaque installation.
