#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PharmaVeille - Gestion des dates de peremption en officine
-----------------------------------------------------------
Fonctionnalites :
  * Saisie des medicaments (nom, lot, quantite, date de peremption, prix unitaire)
  * Tri automatique par date de peremption (de la plus proche a la plus eloignee)
  * Alertes configurables : 1, 2, 3, 4, 5 ou 6 mois avant peremption (choix multiple)
  * Code couleur : perime (rouge), en alerte (orange), correct (vert)
  * Valeur du stock a risque (quantite x prix unitaire)
  * Recherche, modification, suppression, export CSV

Dependances : aucune (bibliotheque standard Python 3.8+)
Lancement   : python pharma_peremption.py
"""

import csv
import os
import sqlite3
import tkinter as tk
from calendar import monthrange
from datetime import date, datetime
from tkinter import messagebox, ttk, filedialog

# __file__ n'existe pas dans un notebook Jupyter : on retombe sur le dossier courant.
try:
    DOSSIER = os.path.dirname(os.path.abspath(__file__))
except NameError:
    DOSSIER = os.getcwd()
DB_FILE = os.path.join(DOSSIER, "pharmacie.db")
SEUILS_POSSIBLES = [1, 2, 3, 4, 5, 6]      # en mois
DATE_FMT = "%d/%m/%Y"


# --------------------------------------------------------------------------
#  Utilitaires de dates
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
    txt = txt.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    raise ValueError("Format de date invalide (attendu : JJ/MM/AAAA)")


def jours_restants(exp: date) -> int:
    return (exp - date.today()).days


# --------------------------------------------------------------------------
#  Couche base de donnees
# --------------------------------------------------------------------------
class BaseDonnees:
    def __init__(self, chemin=DB_FILE):
        self.conn = sqlite3.connect(chemin)
        self.conn.row_factory = sqlite3.Row
        self._creer_tables()

    def _creer_tables(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS medicaments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                nom          TEXT    NOT NULL,
                lot          TEXT    DEFAULT '',
                quantite     INTEGER NOT NULL DEFAULT 1,
                peremption   TEXT    NOT NULL,          -- ISO AAAA-MM-JJ
                prix         REAL    NOT NULL DEFAULT 0
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS parametres (
                cle    TEXT PRIMARY KEY,
                valeur TEXT
            )""")
        self.conn.commit()

    # ---- medicaments -----------------------------------------------------
    def ajouter(self, nom, lot, quantite, peremption: date, prix):
        self.conn.execute(
            "INSERT INTO medicaments (nom, lot, quantite, peremption, prix) VALUES (?,?,?,?,?)",
            (nom, lot, quantite, peremption.isoformat(), prix))
        self.conn.commit()

    def modifier(self, id_, nom, lot, quantite, peremption: date, prix):
        self.conn.execute(
            "UPDATE medicaments SET nom=?, lot=?, quantite=?, peremption=?, prix=? WHERE id=?",
            (nom, lot, quantite, peremption.isoformat(), prix, id_))
        self.conn.commit()

    def supprimer(self, id_):
        self.conn.execute("DELETE FROM medicaments WHERE id=?", (id_,))
        self.conn.commit()

    def lister(self, recherche=""):
        """Retourne les lignes TRIEES par date de peremption croissante."""
        sql = "SELECT * FROM medicaments"
        params = []
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
        return [3, 6]                       # valeurs par defaut

    def set_seuils(self, seuils):
        val = ",".join(str(s) for s in sorted(seuils))
        self.conn.execute(
            "INSERT INTO parametres (cle, valeur) VALUES ('seuils', ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur", (val,))
        self.conn.commit()


# --------------------------------------------------------------------------
#  Logique d'alerte
# --------------------------------------------------------------------------
def statut(peremption: date, seuils):
    """
    Renvoie (code, libelle) :
      'perime'  -> date depassee
      'alerte'  -> dans la fenetre d'un des seuils selectionnes
      'ok'      -> rien a signaler
    Le seuil retenu est le plus petit seuil declenche (le plus urgent).
    """
    aujourdhui = date.today()
    if peremption < aujourdhui:
        return "perime", "PERIME"
    for s in sorted(seuils):
        if aujourdhui >= ajouter_mois(peremption, -s):
            return "alerte", f"< {s} mois"
    return "ok", "OK"


# --------------------------------------------------------------------------
#  Interface graphique
# --------------------------------------------------------------------------
class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PharmaVeille - Suivi des peremptions")
        self.geometry("1050x640")
        self.minsize(900, 520)

        self.db = BaseDonnees()
        self.seuils = self.db.get_seuils()
        self.id_selection = None

        self._construire_interface()
        self.rafraichir()
        self.after(400, self.alerte_demarrage)

    # ---- construction ----------------------------------------------------
    def _construire_interface(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

        # ---------- Formulaire de saisie ----------
        cadre_saisie = ttk.LabelFrame(self, text=" Saisie d'un produit ", padding=10)
        cadre_saisie.pack(fill="x", padx=10, pady=(10, 5))

        self.var_nom = tk.StringVar()
        self.var_lot = tk.StringVar()
        self.var_qte = tk.StringVar(value="1")
        self.var_date = tk.StringVar()
        self.var_prix = tk.StringVar(value="0.00")

        champs = [
            ("Medicament *", self.var_nom, 26),
            ("Lot", self.var_lot, 12),
            ("Quantite", self.var_qte, 7),
            ("Peremption (JJ/MM/AAAA) *", self.var_date, 14),
            ("Prix unitaire (EUR)", self.var_prix, 9),
        ]
        for col, (label, var, largeur) in enumerate(champs):
            ttk.Label(cadre_saisie, text=label).grid(row=0, column=col, sticky="w", padx=4)
            ttk.Entry(cadre_saisie, textvariable=var, width=largeur).grid(
                row=1, column=col, sticky="w", padx=4)

        zone_btn = ttk.Frame(cadre_saisie)
        zone_btn.grid(row=1, column=len(champs), padx=(15, 0))
        ttk.Button(zone_btn, text="Ajouter", command=self.ajouter).pack(side="left", padx=2)
        self.btn_maj = ttk.Button(zone_btn, text="Modifier", command=self.modifier,
                                  state="disabled")
        self.btn_maj.pack(side="left", padx=2)
        ttk.Button(zone_btn, text="Vider", command=self.vider_formulaire).pack(side="left", padx=2)

        # ---------- Parametres d'alerte ----------
        cadre_alerte = ttk.LabelFrame(
            self, text=" Alertes : prevenir combien de mois avant la peremption ? ", padding=10)
        cadre_alerte.pack(fill="x", padx=10, pady=5)

        self.vars_seuils = {}
        for i, s in enumerate(SEUILS_POSSIBLES):
            v = tk.BooleanVar(value=(s in self.seuils))
            self.vars_seuils[s] = v
            ttk.Checkbutton(cadre_alerte, text=f"{s} mois", variable=v,
                            command=self.maj_seuils).grid(row=0, column=i, padx=10)

        ttk.Separator(cadre_alerte, orient="vertical").grid(row=0, column=6, sticky="ns", padx=12)
        ttk.Button(cadre_alerte, text="Voir les alertes",
                   command=self.afficher_alertes).grid(row=0, column=7, padx=4)
        ttk.Button(cadre_alerte, text="Exporter CSV",
                   command=self.exporter_csv).grid(row=0, column=8, padx=4)

        # ---------- Recherche ----------
        barre = ttk.Frame(self)
        barre.pack(fill="x", padx=10, pady=(5, 0))
        ttk.Label(barre, text="Rechercher :").pack(side="left")
        self.var_recherche = tk.StringVar()
        self.var_recherche.trace_add("write", lambda *_: self.rafraichir())
        ttk.Entry(barre, textvariable=self.var_recherche, width=32).pack(side="left", padx=6)
        ttk.Button(barre, text="Supprimer la ligne",
                   command=self.supprimer).pack(side="right")

        # ---------- Tableau ----------
        cadre_tab = ttk.Frame(self)
        cadre_tab.pack(fill="both", expand=True, padx=10, pady=8)

        colonnes = ("nom", "lot", "qte", "peremption", "jours", "prix", "valeur", "statut")
        entetes = {
            "nom": ("Medicament", 220), "lot": ("Lot", 90), "qte": ("Qte", 60),
            "peremption": ("Peremption", 110), "jours": ("Jours restants", 110),
            "prix": ("Prix unit.", 90), "valeur": ("Valeur totale", 110),
            "statut": ("Statut", 110),
        }
        self.tableau = ttk.Treeview(cadre_tab, columns=colonnes, show="headings",
                                    selectmode="browse")
        for c in colonnes:
            titre, largeur = entetes[c]
            self.tableau.heading(c, text=titre)
            ancre = "w" if c in ("nom", "lot") else "center"
            self.tableau.column(c, width=largeur, anchor=ancre)

        defil = ttk.Scrollbar(cadre_tab, orient="vertical", command=self.tableau.yview)
        self.tableau.configure(yscrollcommand=defil.set)
        self.tableau.pack(side="left", fill="both", expand=True)
        defil.pack(side="right", fill="y")

        self.tableau.tag_configure("perime", background="#f8c9c4")
        self.tableau.tag_configure("alerte", background="#fde3b0")
        self.tableau.tag_configure("ok", background="#d8f0d3")
        self.tableau.bind("<<TreeviewSelect>>", self.on_selection)

        # ---------- Barre de statut ----------
        self.var_statut = tk.StringVar()
        ttk.Label(self, textvariable=self.var_statut, relief="sunken",
                  anchor="w", padding=4).pack(fill="x", side="bottom")

    # ---- actions ---------------------------------------------------------
    def lire_formulaire(self):
        nom = self.var_nom.get().strip()
        if not nom:
            raise ValueError("Le nom du medicament est obligatoire.")
        peremption = parse_date(self.var_date.get())
        try:
            qte = int(self.var_qte.get() or 1)
            if qte < 0:
                raise ValueError
        except ValueError:
            raise ValueError("La quantite doit etre un entier positif.")
        try:
            prix = float(self.var_prix.get().replace(",", ".") or 0)
        except ValueError:
            raise ValueError("Le prix doit etre un nombre (ex : 12.50).")
        return nom, self.var_lot.get().strip(), qte, peremption, prix

    def ajouter(self):
        try:
            self.db.ajouter(*self.lire_formulaire())
        except ValueError as e:
            messagebox.showerror("Saisie incorrecte", str(e))
            return
        self.vider_formulaire()
        self.rafraichir()

    def modifier(self):
        if self.id_selection is None:
            return
        try:
            self.db.modifier(self.id_selection, *self.lire_formulaire())
        except ValueError as e:
            messagebox.showerror("Saisie incorrecte", str(e))
            return
        self.vider_formulaire()
        self.rafraichir()

    def supprimer(self):
        if self.id_selection is None:
            messagebox.showinfo("Suppression", "Selectionnez d'abord une ligne.")
            return
        if messagebox.askyesno("Confirmation", "Supprimer definitivement cette ligne ?"):
            self.db.supprimer(self.id_selection)
            self.vider_formulaire()
            self.rafraichir()

    def vider_formulaire(self):
        self.id_selection = None
        self.btn_maj.config(state="disabled")
        self.var_nom.set("")
        self.var_lot.set("")
        self.var_qte.set("1")
        self.var_date.set("")
        self.var_prix.set("0.00")
        if self.tableau.selection():
            self.tableau.selection_remove(self.tableau.selection())

    def on_selection(self, _evt=None):
        sel = self.tableau.selection()
        if not sel:
            return
        self.id_selection = int(sel[0])
        ligne = self.db.conn.execute(
            "SELECT * FROM medicaments WHERE id=?", (self.id_selection,)).fetchone()
        if ligne:
            self.var_nom.set(ligne["nom"])
            self.var_lot.set(ligne["lot"])
            self.var_qte.set(str(ligne["quantite"]))
            self.var_date.set(date.fromisoformat(ligne["peremption"]).strftime(DATE_FMT))
            self.var_prix.set(f"{ligne['prix']:.2f}")
            self.btn_maj.config(state="normal")

    def maj_seuils(self):
        self.seuils = [s for s, v in self.vars_seuils.items() if v.get()]
        self.db.set_seuils(self.seuils)
        self.rafraichir()

    # ---- affichage -------------------------------------------------------
    def rafraichir(self):
        self.tableau.delete(*self.tableau.get_children())
        lignes = self.db.lister(self.var_recherche.get().strip())

        nb_perimes = nb_alertes = 0
        valeur_risque = 0.0

        for l in lignes:                                    # deja trie par peremption
            exp = date.fromisoformat(l["peremption"])
            code, libelle = statut(exp, self.seuils)
            valeur = l["quantite"] * l["prix"]
            if code == "perime":
                nb_perimes += 1
                valeur_risque += valeur
            elif code == "alerte":
                nb_alertes += 1
                valeur_risque += valeur

            self.tableau.insert(
                "", "end", iid=str(l["id"]), tags=(code,),
                values=(l["nom"], l["lot"], l["quantite"],
                        exp.strftime(DATE_FMT), jours_restants(exp),
                        f"{l['prix']:.2f} EUR", f"{valeur:.2f} EUR", libelle))

        seuils_txt = ", ".join(f"{s} mois" for s in self.seuils) or "aucun"
        self.var_statut.set(
            f"{len(lignes)} produit(s)  |  {nb_perimes} perime(s)  |  "
            f"{nb_alertes} en alerte  |  Valeur a risque : {valeur_risque:.2f} EUR  |  "
            f"Seuils actifs : {seuils_txt}")

    def lignes_en_alerte(self):
        res = []
        for l in self.db.lister():
            exp = date.fromisoformat(l["peremption"])
            code, libelle = statut(exp, self.seuils)
            if code in ("perime", "alerte"):
                res.append((l, exp, libelle))
        return res

    def afficher_alertes(self):
        alertes = self.lignes_en_alerte()
        if not alertes:
            messagebox.showinfo("Alertes", "Aucun produit ne declenche d'alerte.")
            return
        lignes = [f"- {l['nom']} (lot {l['lot'] or '-'}) : {exp.strftime(DATE_FMT)} "
                  f"-> {lib}, {jours_restants(exp)} j, {l['quantite'] * l['prix']:.2f} EUR"
                  for l, exp, lib in alertes[:25]]
        suite = "" if len(alertes) <= 25 else f"\n... et {len(alertes) - 25} autre(s)."
        messagebox.showwarning(
            "Produits a surveiller",
            f"{len(alertes)} produit(s) concerne(s) :\n\n" + "\n".join(lignes) + suite)

    def alerte_demarrage(self):
        if self.lignes_en_alerte():
            self.afficher_alertes()

    def exporter_csv(self):
        chemin = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("Fichier CSV", "*.csv")],
            initialfile=f"peremptions_{date.today().isoformat()}.csv")
        if not chemin:
            return
        with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Medicament", "Lot", "Quantite", "Peremption",
                        "Jours restants", "Prix unitaire", "Valeur totale", "Statut"])
            for l in self.db.lister():
                exp = date.fromisoformat(l["peremption"])
                _, lib = statut(exp, self.seuils)
                w.writerow([l["nom"], l["lot"], l["quantite"], exp.strftime(DATE_FMT),
                            jours_restants(exp), f"{l['prix']:.2f}",
                            f"{l['quantite'] * l['prix']:.2f}", lib])
        messagebox.showinfo("Export", f"Fichier enregistre :\n{chemin}")


if __name__ == "__main__":
    Application().mainloop()
