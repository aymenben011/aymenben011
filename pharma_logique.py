# -*- coding: utf-8 -*-
"""
Logique metier de PharmaVeille : dates, statuts d'alerte, acces SQLite.
Aucune dependance a une interface : reutilisable en Tkinter, Streamlit, Dash ou en test.
"""

import os
import sqlite3
from calendar import monthrange
from datetime import date, datetime

SEUILS_POSSIBLES = [1, 2, 3, 4, 5, 6]        # en mois
SEUILS_DEFAUT = [3, 6]
DATE_FMT = "%d/%m/%Y"

try:
    DOSSIER = os.path.dirname(os.path.abspath(__file__))
except NameError:
    DOSSIER = os.getcwd()
DB_FILE = os.environ.get("PHARMA_DB", os.path.join(DOSSIER, "pharmacie.db"))


# --------------------------------------------------------------------------
#  Dates
# --------------------------------------------------------------------------
def ajouter_mois(d: date, n: int) -> date:
    """Decale une date de n mois (n peut etre negatif), en gerant les fins de mois."""
    mois_total = d.month - 1 + n
    annee = d.year + mois_total // 12
    mois = mois_total % 12 + 1
    jour = min(d.day, monthrange(annee, mois)[1])
    return date(annee, mois, jour)


def parse_date(txt: str) -> date:
    """Accepte JJ/MM/AAAA, JJ-MM-AAAA ou AAAA-MM-JJ."""
    txt = str(txt).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    raise ValueError("Format de date invalide (attendu : JJ/MM/AAAA)")


def jours_restants(exp: date) -> int:
    return (exp - date.today()).days


def statut(peremption: date, seuils):
    """
    Renvoie (code, libelle) :
      'perime'  -> date depassee
      'alerte'  -> dans la fenetre d'un des seuils selectionnes
      'ok'      -> rien a signaler
    Le seuil retenu est le plus petit declenche, donc le plus urgent.
    """
    aujourdhui = date.today()
    if peremption < aujourdhui:
        return "perime", "PERIME"
    for s in sorted(seuils):
        if aujourdhui >= ajouter_mois(peremption, -s):
            return "alerte", f"< {s} mois"
    return "ok", "OK"


# --------------------------------------------------------------------------
#  Base de donnees
# --------------------------------------------------------------------------
class BaseDonnees:
    def __init__(self, chemin=DB_FILE):
        self.chemin = chemin
        self.conn = sqlite3.connect(chemin, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._creer_tables()

    def _creer_tables(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS medicaments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nom        TEXT    NOT NULL,
                lot        TEXT    DEFAULT '',
                quantite   INTEGER NOT NULL DEFAULT 1,
                peremption TEXT    NOT NULL,
                prix       REAL    NOT NULL DEFAULT 0
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS parametres (
                cle TEXT PRIMARY KEY, valeur TEXT
            )""")
        self.conn.commit()

    # ---- CRUD ------------------------------------------------------------
    def ajouter(self, nom, lot, quantite, peremption: date, prix):
        self.conn.execute(
            "INSERT INTO medicaments (nom, lot, quantite, peremption, prix) VALUES (?,?,?,?,?)",
            (nom.strip(), lot.strip(), int(quantite), peremption.isoformat(), float(prix)))
        self.conn.commit()

    def modifier(self, id_, nom, lot, quantite, peremption: date, prix):
        self.conn.execute(
            "UPDATE medicaments SET nom=?, lot=?, quantite=?, peremption=?, prix=? WHERE id=?",
            (nom.strip(), lot.strip(), int(quantite), peremption.isoformat(),
             float(prix), int(id_)))
        self.conn.commit()

    def supprimer(self, ids):
        if isinstance(ids, int):
            ids = [ids]
        self.conn.executemany("DELETE FROM medicaments WHERE id=?",
                              [(int(i),) for i in ids])
        self.conn.commit()

    def lister(self, recherche=""):
        """Lignes TRIEES par date de peremption croissante."""
        sql, params = "SELECT * FROM medicaments", []
        if recherche:
            sql += " WHERE nom LIKE ? OR lot LIKE ?"
            params = [f"%{recherche}%", f"%{recherche}%"]
        sql += " ORDER BY date(peremption) ASC, nom ASC"
        return self.conn.execute(sql, params).fetchall()

    # ---- parametres ------------------------------------------------------
    def get_seuils(self):
        row = self.conn.execute(
            "SELECT valeur FROM parametres WHERE cle='seuils'").fetchone()
        if row and row["valeur"]:
            return sorted(int(x) for x in row["valeur"].split(",") if x)
        return list(SEUILS_DEFAUT)

    def set_seuils(self, seuils):
        self.conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES ('seuils', ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
            (",".join(str(s) for s in sorted(seuils)),))
        self.conn.commit()


# --------------------------------------------------------------------------
#  Synthese
# --------------------------------------------------------------------------
def enrichir(lignes, seuils):
    """Transforme les lignes SQL en dictionnaires prets a afficher."""
    res = []
    for l in lignes:
        exp = date.fromisoformat(l["peremption"])
        code, libelle = statut(exp, seuils)
        res.append({
            "id": l["id"], "Medicament": l["nom"], "Lot": l["lot"],
            "Quantite": l["quantite"], "Peremption": exp,
            "Jours restants": jours_restants(exp),
            "Prix unitaire": round(l["prix"], 2),
            "Valeur totale": round(l["quantite"] * l["prix"], 2),
            "Statut": libelle, "_code": code,
        })
    return res


def indicateurs(donnees):
    """Compteurs et valeur du stock a risque."""
    perimes = [d for d in donnees if d["_code"] == "perime"]
    alertes = [d for d in donnees if d["_code"] == "alerte"]
    return {
        "total": len(donnees),
        "perimes": len(perimes),
        "alertes": len(alertes),
        "valeur_risque": round(sum(d["Valeur totale"] for d in perimes + alertes), 2),
        "valeur_stock": round(sum(d["Valeur totale"] for d in donnees), 2),
    }
