import streamlit as st
import osmnx as ox
import networkx as nx
import folium
from folium.plugins import HeatMap, AntPath
import streamlit.components.v1 as components
from streamlit_js_eval import get_geolocation
import requests
import numpy as np

# === CONFIGURARE INTERFAȚĂ LUMINOASĂ ===
st.set_page_config(page_title="SENTINEL V15 - DAYLIGHT", layout="wide")
st.markdown("""
<style>
/* Fundal deschis, text închis pentru vizibilitate maximă */
.main {background-color: #F4F6F9; color: #1E1E1E; font-family: 'Courier New', monospace;}
h1, h2, h3 {color: #0A5C36; text-transform: uppercase; font-weight: bold;}
/* Cardurile de telemetrie re-stilizate pentru tema de zi */
.hud-card {background: #FFFFFF; border-left: 5px solid #0A5C36; padding: 15px; margin-bottom: 10px; border-radius: 5px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); font-size: 14px; color: #1E1E1E;}
/* Stil pentru a asigura că textul din meniurile de selecție e negru */
div[data-baseweb="select"] {color: black;}
</style>
""", unsafe_allow_html=True)

st.title("🛰️ SENTINEL‑GEOCHEM — TACTICAL ROUTING")

# === 1️⃣ ACHIZIȚIE LOCAȚIE ===
loc = get_geolocation()
if not loc:
    st.warning("📡 Inițializare senzori GPS... Te rog permite accesul la locație pe telefon.")
    st.stop()

USER_LAT = loc["coords"]["latitude"]
USER_LON = loc["coords"]["longitude"]

# === 2️⃣ WEATHER ENGINE ===
@st.cache_data(ttl=300)
def get_live_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=wind_speed_10m,wind_direction_10m"
        r = requests.get(url, timeout=10).json()
        return float(r["current"]["wind_speed_10m"]), float(r["current"]["wind_direction_10m"])
    except:
        return 4.5, 225.0

# === 3️⃣ MATRICEA SUBSTANȚELOR ===
CHIMIC = {
    "CLOR (GAZ GREU)": {"dens": 3.17, "color": "#7CFC00", "logic": 2.0, "desc": "Se depune în văi. Fugă contra vântului."},
    "AMONIAC (GAZ UȘOR)": {"dens": 0.73, "color": "#00FFFF", "logic": 1.0, "desc": "Se ridică rapid. Fugă laterală."},
    "AGENT VX (PERSISTENT)": {"dens": 5.0, "color": "#FF0000", "logic": 2.2, "desc": "Letalitate maximă la sol. Evacuare urgentă upwind."},
    "IOD-131 (PARTICULE)": {"dens": 1.0, "color": "#8A2BE2", "logic": 1.5, "desc": "Dispersie mediană. Evacuare diagonală."}
}

# === 4️⃣ PANOU DE CONTROL (MUTAT SUS, VIZIBIL PE TELEFON) ===
st.markdown("### ⚙️ SETĂRI PARAMETRI INCIDENT")
col_setari1, col_setari2 = st.columns(2)

with col_setari1:
    subst_name = st.selectbox("AGENT CHIMIC", list(CHIMIC.keys()))
with col_setari2:
    map_style = st.radio("VIZUALIZARE HARTĂ", ["Standard", "Satellite", "Dark Tactical"], horizontal=True)

current_info = CHIMIC[subst_name]
st.markdown("---")

# === 5️⃣ MOTORUL DE RUTARE TACTICĂ ===
@st.cache_data
def calculate_optimized_evacuation(lat, lon, v_dir, v_speed, subst_info):
    G = ox.graph_from_point((lat, lon), dist=3500, network_type="drive")
    start_node = ox.nearest_nodes(G, lon, lat)
    
    rad_v = np.radians(v_dir)
    logic = subst_info["logic"]
    dens = subst_info["dens"]

    shift_angle = np.pi if logic > 1.8 else (np.pi / 2)
    dest_lat = lat + 0.03 * np.cos(rad_v + shift_angle)
    dest_lon = lon + 0.03 * np.sin(rad_v + shift_angle)
    target_node = ox.nearest_nodes(G, dest_lon, dest_lat)

    def tactical_weight(u, v, data):
        length = data[0].get('length', 10)
        node_data = G.nodes[v]
        n_lat, n_lon = node_data['y'], node_data['x']
        
        dx = n_lon - lon
        dy = n_lat - lat
        angle_to_node = np.arctan2(dx, dy)
        
        diff = np.abs(np.degrees(angle_to_node) - v_dir) % 360
        if diff > 180: diff = 360 - diff
        
        risk_factor = 1.0
        if diff < 30:
            risk_factor = 500.0 * (dens / 0.73)
        elif diff < 60:
            risk_factor = 50.0 * (dens / 0.73)
            
        return length * risk_factor

    ruta = nx.shortest_path(G, start_node, target_node, weight=tactical_weight)
    return G, ruta, (G.nodes[target_node]["y"], G.nodes[target_node]["x"])

# === 6️⃣ EXECUȚIE SIMULARE ===
try:
    v_speed, v_dir = get_live_weather(USER_LAT, USER_LON)
    
    with st.spinner('Calculez rutele de evacuare...'):
        G, ruta_ids, dest_coords = calculate_optimized_evacuation(USER_LAT, USER_LON, v_dir, v_speed, current_info)

    # Configurare Hartă
    if map_style == "Dark Tactical": tiles = "CartoDB dark_matter"
    elif map_style == "Satellite": tiles = "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}"
    else: tiles = "OpenStreetMap"
    
    m = folium.Map(location=[USER_LAT, USER_LON], zoom_start=15, tiles=tiles, attr="Sentinel Ops")

    # NOR TOXIC
    heat_points = []
    rad_v = np.radians(v_dir)
    impact_dist = v_speed * 180 * (1.5 if current_info["dens"] < 1 else 1.0)
    
    for d in np.linspace(0, impact_dist, 45):
        spread = 0.001 + (d / impact_dist) * (0.015 if current_info["dens"] > 1.5 else 0.008)
        for u in np.linspace(-spread, spread, 5):
            p_lat = USER_LAT + (d / 111320) * np.cos(rad_v) + u
            p_lon = USER_LON + (d / (111320 * np.cos(np.radians(USER_LAT)))) * np.sin(rad_v)
            weight = 1 - (d / impact_dist)
            heat_points.append([p_lat, p_lon, weight])

    HeatMap(heat_points, radius=25, blur=20, min_opacity=0.3, 
            gradient={0.4: current_info["color"], 1: '#FF0000'}).add_to(m)

    # RUTA SIGURĂ (Verde închis pentru contrast)
    ruta_coords = [[G.nodes[n]['y'], G.nodes[n]['x']] for n in ruta_ids]
    AntPath(ruta_coords, color="#0A5C36", weight=8, delay=400, pulse_color="#00FF00").add_to(m)

    # MARKERI
    folium.Marker([USER_LAT, USER_LON], icon=folium.Icon(color='red', icon='biohazard', prefix='fa')).add_to(m)
    folium.Marker(dest_coords, icon=folium.Icon(color='green', icon='shield', prefix='fa')).add_to(m)

    # SĂGEATĂ VÂNT
    v_tip = [USER_LAT + 0.008 * np.cos(np.radians(v_dir)), USER_LON + 0.008 * np.sin(np.radians(v_dir))]
    folium.PolyLine([[USER_LAT, USER_LON], v_tip], color="blue", weight=4).add_to(m)

    # AFIȘARE
    st.markdown("### 📊 TELEMETRIE ȘI HARTĂ")
    col_m, col_h = st.columns([3, 1])
    
    with col_m:
        components.html(m._repr_html_(), height=500)

    with col_h:
        st.markdown(f"""
        <div class='hud-card'>💨 <b>VÂNT:</b> {v_speed} m/s<br>🧭 <b>DIRECȚIE:</b> {v_dir}°</div>
        <div class='hud-card'>⚖️ <b>DENSITATE GAZ:</b> {current_info['dens']} ρ</div>
        <div class='hud-card'>🛡️ <b>STRATEGIE:</b><br>{ 'UPWIND (Contra vânt)' if current_info['logic']>1.8 else 'CROSSWIND (Lateral)' }<br><br><i>{current_info['desc']}</i></div>
        """, unsafe_allow_html=True)
        
except Exception as e:
    st.error(f"Eroare Senzor: {e}")
