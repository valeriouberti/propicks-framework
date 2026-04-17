"""Streamlit dashboard per il trading framework.

Wrapper UI delle funzioni di ``domain``, ``io``, ``ai`` e ``reports`` che la
CLI già espone. Non sostituisce la CLI — è un layer parallelo per chi preferisce
form/tabelle a argparse. Tutta la logica di business resta nei layer puri
sottostanti.

Entry point: ``propicks-dashboard`` (vedi ``launcher.py``).
"""
