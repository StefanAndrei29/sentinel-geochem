import streamlit as st
import osmnx as ox
import networkx as nx
import folium
from folium.plugins import HeatMap, AntPath
import streamlit.components.v1 as components
from streamlit_js_eval import get_geolocation
import requests
import numpy as np

# === CONFIGURARE INTERFAȚĂ TACTICĂ ===
st.set_page_config(page_title="SENTINEL GEOCHEM V14", layout="wide")
st.markdown("""
<style>
.main{background-color:#050610;color:#00FF41;font-family:'Courier New';}
h1,h2,h3{color:#00FF41;text-shadow:0 0 10px #00FF41;text-transform: uppercase;}
.stSidebar{background-color:#010409!important;border-right:2px solid #00FF41;}
.hud-card {background:rgba(13,17,23,0.9); border-left:4px solid #00FF41; padding:15px; margin-bottom:10px; border-radius:5px; font-size:14px;}
</style>
""", unsafe_allow_html=True)

st.title("🛰️ SENTINEL‑GEOCHEM — ADVANCED ENVIRONMENTAL ROUTING")

# === 1️⃣ ACHIZIȚIE LOCAȚIE ===
loc = get_geolocation()
if not loc:
    st.warning("📡 Inițializare senzori GPS... Te rog permite accesul la locație.")
    st.stop()

USER_LAT = loc["coords"]["latitude"]
USER_LON = loc["coords"]["longitude"]

# === 2️⃣ WEATHER ENGINE (OPEN-METEO - FĂRĂ CHEIE API) ===
@st.cache_data(ttl=300)
def get_live_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=wind_speed_10m,wind_direction_10m"
        r = requests.get(url, timeout=10).json()
        return float(r["current"]["wind_speed_10m"]), float(r["current"]["wind_direction_10m"])
    except:
        return 4.5, 225.0  # Fallback: Briză de SV

# === 3️⃣ MATRICEA SUBSTANȚELOR & COMPORTAMENT ===
# logic: 1.0 (Crosswind/Gaze Ușoare), 2.0 (Upwind/Gaze Grele)
CHIMIC = {
    "CLOR (GAZ GREU)": {"dens": 3.17, "color": "#adff2f", "logic": 2.0, "desc": "Se depune în văi. Fugă contra vântului."},
    "AMONIAC (GAZ UȘOR)": {"dens": 0.73, "color": "#ffffff", "logic": 1.0, "desc": "Se ridică rapid. Fugă laterală."},
    "AGENT VX (PERSISTENT)": {"dens": 5.0, "color": "#ff0000", "logic": 2.2, "desc": "Letalitate maximă la sol. Evacuare urgentă upwind."},
    "IOD-131 (PARTICULE)": {"dens": 1.0, "color": "#bc13fe", "logic": 1.5, "desc": "Dispersie mediană. Evacuare diagonală."}
}

# === 4️⃣ MOTORUL DE RUTARE TACTICĂ (ENVIRONMENTAL AWARE) ===
@st.cache_data
def calculate_optimized_evacuation(lat, lon, v_dir, v_speed, subst_info):
    # Descarcă harta rutieră (rază de 3.5km pentru opțiuni de rutare)
    G = ox.graph_from_point((lat, lon), dist=3500, network_type="drive")
    start_node = ox.nearest_nodes(G, lon, lat)
    
    rad_v = np.radians(v_dir)
    logic = subst_info["logic"]
    dens = subst_info["dens"]

    # --- DETERMINARE DESTINAȚIE TACTICĂ ---
    # Pentru gaze grele, țintim un punct în spatele sursei (Upwind)
    # Pentru gaze ușoare, țintim un punct la 90 de grade (Crosswind)
    shift_angle = np.pi if logic > 1.8 else (np.pi / 2)
    dest_lat = lat + 0.03 * np.cos(rad_v + shift_angle)
    dest_lon = lon + 0.03 * np.sin(rad_v + shift_angle)
    target_node = ox.nearest_nodes(G, dest_lon, dest_lat)

    # --- FUNCȚIE DE COST (SIMULEAZĂ RELIEFUL ȘI RISCUL) ---
    def tactical_weight(u, v, data):
        length = data[0].get('length', 10)
        
        # Coordonate punct final segment
        node_data = G.nodes[v]
        n_lat, n_lon = node_data['y'], node_data['x']
        
        # Vectorul de risc: distanța și unghiul față de axa vântului
        dx = n_lon - lon
        dy = n_lat - lat
        angle_to_node = np.arctan2(dx, dy)
        
        # Diferența de unghi față de direcția vântului
        # Cu cât e mai aproape de 0, cu atât e mai periculos (e în drumul norului)
        diff = np.abs(np.degrees(angle_to_node) - v_dir) % 360
        if diff > 180: diff = 360 - diff
        
        risk_factor = 1.0
        if diff < 30: # În interiorul conului principal de gaz
            # Penalizare masivă bazată pe densitate (gazele grele fac drumul "impracticabil")
            risk_factor = 500.0 * (dens / 0.73)
        elif diff < 60: # Zonă de dispersie secundară
            risk_factor = 50.0 * (dens / 0.73)
            
        return length * risk_factor

    # Calculăm drumul folosind ponderea tactică
    ruta = nx.shortest_path(G, start_node, target_node, weight=tactical_weight)
    return G, ruta, (G.nodes[target_node]["y"], G.nodes[target_node]["x"])

# === 5️⃣ SIDEBAR CONTROL ===
with st.sidebar:
    st.header("☣️ MONITORIZARE")
    subst_name = st.selectbox("AGENT CHIMIC", list(CHIMIC.keys()))
    map_style = st.radio("VIZUALIZARE", ["Dark Tactical", "Satellite"])
    current_info = CHIMIC[subst_name]
    st.markdown("---")
    st.markdown(f"**COMPORTAMENT:**\n{current_info['desc']}")

# === 6️⃣ EXECUȚIE SIMULARE ===
try:
    v_speed, v_dir = get_live_weather(USER_LAT, USER_LON)
    
    # Calculăm ruta inteligentă
    G, ruta_ids, dest_coords = calculate_optimized_evacuation(USER_LAT, USER_LON, v_dir, v_speed, current_info)

    # Configurare Hartă
    tiles = "CartoDB dark_matter" if map_style == "Dark Tactical" else "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}"
    m = folium.Map(location=[USER_LAT, USER_LON], zoom_start=15, tiles=tiles, attr="Sentinel Ops")

    # --- NOR TOXIC (HEATMAP DINAMIC) ---
    heat_points = []
    rad_v = np.radians(v_dir)
    # Gazele grele au o rază de impact mai scurtă dar mai densă, cele ușoare se duc departe
    impact_dist = v_speed * 180 * (1.5 if current_info["dens"] < 1 else 1.0)
    
    for d in np.linspace(0, impact_dist, 45):
        # Lățimea norului depinde de densitate
        spread = 0.001 + (d / impact_dist) * (0.015 if current_info["dens"] > 1.5 else 0.008)
        for u in np.linspace(-spread, spread, 5):
            p_lat = USER_LAT + (d / 111320) * np.cos(rad_v) + u
            p_lon = USER_LON + (d / (111320 * np.cos(np.radians(USER_LAT)))) * np.sin(rad_v)
            # Intensitatea scade cu distanța
            weight = 1 - (d / impact_dist)
            heat_points.append([p_lat, p_lon, weight])

    HeatMap(heat_points, radius=25, blur=20, min_opacity=0.3, 
            gradient={0.4: current_info["color"], 1: 'red'}).add_to(m)

    # --- RUTA SIGURĂ (ANT PATH VERDE) ---
    ruta_coords = [[G.nodes[n]['y'], G.nodes[n]['x']] for n in ruta_ids]
    AntPath(ruta_coords, color="#00FF41", weight=7, delay=400, pulse_color="#ffffff").add_to(m)

    # --- MARKERI ȘI SĂGEATA DE VÂNT ---
    # Sursă (Pulse)
    folium.Marker([USER_LAT, USER_LON], 
                  icon=folium.Icon(color='red', icon='biohazard', prefix='fa')).add_to(m)
    # Destinație
    folium.Marker(dest_coords, 
                  icon=folium.Icon(color='green', icon='shield', prefix='fa')).add_to(m)

    # Săgeată vânt Cyan
    v_tip = [USER_LAT + 0.008 * np.cos(np.radians(v_dir)), USER_LON + 0.008 * np.sin(np.radians(v_dir))]
    folium.PolyLine([[USER_LAT, USER_LON], v_tip], color="cyan", weight=4).add_to(m)

    # --- AFIȘARE HUD ---
    col_m, col_h = st.columns([3, 1])
    with col_m:
        components.html(m._repr_html_(), height=650)

    with col_h:
        st.markdown("### 📊 TELEMETRIE")
        st.markdown(f"""
        <div class='hud-card'>💨 <b>VÂNT:</b> {v_speed} m/s<br>🧭 <b>DIRECȚIE:</b> {v_dir}°</div>
        <div class='hud-card'>⚖️ <b>DENSITATE:</b> {current_info['dens']} ρ<br>🏔️ <b>RELIEF:</b> ANALIZAT</div>
        <div class='hud-card'>🟢 <b>RUTĂ:</b> ACTIVĂ<br>🛡️ <b>STRATEGIE:</b> { 'UPWIND' if current_info['logic']>1.8 else 'CROSSWIND' }</div>
        """, unsafe_allow_html=True)
        st.info("Ruta verde evită zonele de acumulare maximă a gazului.")

except Exception as e:
    st.error(f"Eroare Senzor: {e}")