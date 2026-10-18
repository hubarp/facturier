# Facturier

Facturier de vigneron : factures, clients, produits, déclaration aux douanes, facture électronique (Factur-X) envoyée via la plateforme agréée Super PDP.

- `programme/` : l'application (page `facturier.html` + programme local `facturier.pyw`, Python standard, sans dépendance).
- `Installer le Facturier.bat` : installation sous Windows (Python si besoin, raccourci « Facturier »).
- `versions/` : archives de chaque version (programme seul, et dossier complet pour une première installation).
- `version.json` : manifeste de la dernière version, **signé** (Ed25519). Le Facturier installé le consulte et ne s'installe une mise à jour que si la signature correspond à la clé publique intégrée dans `facturier.pyw`.
- `outils/` : signature des versions (la clé privée reste sur le poste du mainteneur, jamais dans ce dépôt).

Aucune donnée de client ni aucun identifiant ne se trouve dans ce dépôt : les données vivent dans le dossier `donnees/` de chaque installation.
