# -*- coding: utf-8 -*-
"""
PharmaVeille - interface Streamlit
Lancement : streamlit run app_streamlit.py
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from pharma_logique import (SEUILS_POSSIBLES, BaseDonnees, ajouter_mois,
                            enrichir, indicateurs)

st.set_page_config(page_title="PharmaVeille", page_icon="💊", layout="wide")

COULEURS = {"perime": "#f8c9c4", "alerte": "#fde3b0", "ok": "#d8f0d3"}


@st.cache_resource
def get_db():
    """Une seule connexion partagee pour toute la session."""
    return BaseDonnees()


db = get_db()

# --------------------------------------------------------------------------
#  Barre laterale : saisie et parametres
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("Ajouter un produit")
    with st.form("ajout", clear_on_submit=True):
        nom = st.text_input("Medicament *")
        lot = st.text_input("Lot")
        col1, col2 = st.columns(2)
        quantite = col1.number_input("Quantite", min_value=0, value=1, step=1)
        prix = col2.number_input("Prix unitaire (€)", min_value=0.0, value=0.0,
                                 step=0.5, format="%.2f")
        peremption = st.date_input("Date de peremption *",
                                   value=date.today() + timedelta(days=180),
                                   format="DD/MM/YYYY")
        if st.form_submit_button("Ajouter", width="stretch"):
            if not nom.strip():
                st.error("Le nom du medicament est obligatoire.")
            else:
                db.ajouter(nom, lot, quantite, peremption, prix)
                st.success(f"{nom} ajoute.")

    st.divider()
    st.header("Seuils d'alerte")
    st.caption("Prevenir combien de mois avant la peremption ?")
    seuils_actuels = db.get_seuils()
    seuils = st.multiselect("Seuils (choix multiple)", SEUILS_POSSIBLES,
                            default=seuils_actuels,
                            format_func=lambda s: f"{s} mois",
                            label_visibility="collapsed")
    if sorted(seuils) != sorted(seuils_actuels):
        db.set_seuils(seuils)
        st.rerun()

# --------------------------------------------------------------------------
#  Corps de page
# --------------------------------------------------------------------------
st.title("💊 PharmaVeille — suivi des peremptions")

recherche = st.text_input("Rechercher un medicament ou un lot", "")
donnees = enrichir(db.lister(recherche), seuils)
kpi = indicateurs(donnees)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Produits", kpi["total"])
c2.metric("Perimes", kpi["perimes"])
c3.metric("En alerte", kpi["alertes"])
c4.metric("Valeur a risque", f"{kpi['valeur_risque']:.2f} €",
          help="Somme des produits perimes ou en alerte (quantite x prix unitaire)")

if not donnees:
    st.info("Aucun produit enregistre. Utilisez le formulaire dans la barre laterale.")
    st.stop()

# ---- Alerte en tete de page ----
urgents = [d for d in donnees if d["_code"] in ("perime", "alerte")]
if urgents:
    apercu = ", ".join(f"{d['Medicament']} ({d['Statut']})" for d in urgents[:5])
    suite = "" if len(urgents) <= 5 else f" … et {len(urgents) - 5} autre(s)"
    st.warning(f"**{len(urgents)} produit(s) a surveiller :** {apercu}{suite}")

# ---- Tableau trie et colore ----
df = pd.DataFrame(donnees)
codes = df.pop("_code")
ids = df.pop("id")
df["Peremption"] = pd.to_datetime(df["Peremption"])


def colorier(ligne):
    return [f"background-color: {COULEURS[codes.loc[ligne.name]]}"] * len(ligne)


st.subheader("Stock trie par date de peremption")
st.dataframe(
    df.style.apply(colorier, axis=1).format({
        "Peremption": lambda d: d.strftime("%d/%m/%Y"),
        "Prix unitaire": "{:.2f} €", "Valeur totale": "{:.2f} €"}),
    width="stretch", hide_index=True)

st.caption("🔴 perime  🟠 dans une fenetre d'alerte  🟢 correct")

# ---- Export et suppression ----
col_a, col_b = st.columns([1, 2])

with col_a:
    export = df.copy()
    export["Peremption"] = export["Peremption"].dt.strftime("%d/%m/%Y")
    st.download_button(
        "⬇️ Exporter en CSV",
        export.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name=f"peremptions_{date.today().isoformat()}.csv",
        mime="text/csv", width="stretch")

with col_b:
    with st.expander("Supprimer des produits"):
        libelles = {
            f"{d['Medicament']} — lot {d['Lot'] or '-'} — "
            f"{d['Peremption'].strftime('%d/%m/%Y')}": d["id"] for d in donnees}
        choix = st.multiselect("Lignes a supprimer", list(libelles))
        if st.button("Confirmer la suppression", type="primary") and choix:
            db.supprimer([libelles[c] for c in choix])
            st.rerun()

# ---- Repartition par horizon ----
with st.expander("Repartition du stock par horizon"):
    aujourdhui = date.today()
    tranches = {"Perime": 0.0, "< 3 mois": 0.0, "3 a 6 mois": 0.0, "> 6 mois": 0.0}
    for d in donnees:
        exp = d["Peremption"]
        if exp < aujourdhui:
            tranches["Perime"] += d["Valeur totale"]
        elif aujourdhui >= ajouter_mois(exp, -3):
            tranches["< 3 mois"] += d["Valeur totale"]
        elif aujourdhui >= ajouter_mois(exp, -6):
            tranches["3 a 6 mois"] += d["Valeur totale"]
        else:
            tranches["> 6 mois"] += d["Valeur totale"]
    st.bar_chart(pd.Series(tranches, name="Valeur (€)"))
