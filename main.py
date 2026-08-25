import os
import requests
import pandas as pd
import math
from datetime import datetime, timedelta

# =====================================================================
# KONFIGURATION
# =====================================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HOME_LAT = 55.8715
HOME_LON = 9.8242

SKOV_LOCATIONS = {
    "Bygholm Skov / Åbjergskoven": {
        "lat": 55.8645, "lon": 9.7925, 
        "type": "Blandet løv og nål", "jordtype": "blandet"
    },
    "Hansted Skov": {
        "lat": 55.9012, "lon": 9.8390, 
        "type": "Gammel løvskov (Bøg/Eg)", "jordtype": "ler"
    },
    "Boller Skov": {
        "lat": 55.8420, "lon": 9.8840, 
        "type": "Løvskov og fugtige lavninger", "jordtype": "ler"
    },
    "Søvind Skov / Sondrup Bakker": {
        "lat": 55.9180, "lon": 10.0120, 
        "type": "Kuperet terræn, bøg", "jordtype": "ler"
    },
    "Gludsted Plantage (Sandet)": {
        "lat": 56.0450, "lon": 9.3250, 
        "type": "Nåletræsplantage", "jordtype": "sand"
    }
}

def beregn_afstand(lat1, lon1, lat2, lon2):
    R = 6371.0
    rad_lat1, rad_lon1 = math.radians(lat1), math.radians(lon1)
    rad_lat2, rad_lon2 = math.radians(lat2), math.radians(lon2)
    dlat = rad_lat2 - rad_lat1
    dlon = rad_lon2 - rad_lon1
    a = math.sin(dlat / 2)**2 + math.cos(rad_lat1) * math.cos(rad_lat2) * math.sin(dlon / 2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))), 1)

def send_telegram_besked(besked):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": besked, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram fejl: {e}")

def hent_miljo_data(lat, lon):
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&daily=soil_moisture_0_to_7cm,soil_temperature_0_to_7cm,et0_fao_reference_crop_evapotranspiration,relative_humidity_2m_mean"
        f"&past_days=14&forecast_days=7&timezone=Europe%2FCopenhagen"
    )
    try:
        res = requests.get(url, timeout=10).json()
        if "daily" in res:
            return pd.DataFrame(res["daily"])
    except Exception as e:
        print(f"API fejl for {lat}, {lon}: {e}")
    return None

def analyser_svampe_forhold(df, jordtype):
    # Fejlsikret: Returnerer nu alle påkrævede nøgler selv ved manglende data
    if df is None or len(df) < 10:
        return {
            "samlet": 0, "kantarel": 0, "rorhat": 0, 
            "champignon": 0, "temp": 0.0, "status": "Ugyldig data"
        }

    fugt = df["soil_moisture_0_to_7cm"].tolist()
    temp = df["soil_temperature_0_to_7cm"].tolist()
    et0 = df["et0_fao_reference_crop_evapotranspiration"].tolist()

    jord_faktor = {"sand": 0.8, "blandet": 1.0, "ler": 1.2}.get(jordtype, 1.0)

    historisk_fugt = fugt[:14] if len(fugt) >= 14 else fugt
    tidlig_bund = min(historisk_fugt)
    max_nyligt = max(fugt[10:17]) if len(fugt) >= 17 else max(fugt)
    fugt_chok = max(0, max_nyligt - tidlig_bund) * jord_faktor

    snit_fordampning = (sum(et0[10:17]) / 7) if len(et0) >= 17 else 0.2
    tørheds_straffe_faktor = max(0.7, 1.0 - (snit_fordampning * 0.1))

    aktuel_temp = temp[14] if len(temp) > 14 else temp[-1]

    k_temp_score = 1.0 if 12 <= aktuel_temp <= 20 else 0.5
    kantarel_indeks = int(min(100, max(0, ((fugt_chok * 60) + (k_temp_score * 40)) * tørheds_straffe_faktor * 100)))

    r_temp_score = 1.0 if 16 <= aktuel_temp <= 24 else (0.4 if aktuel_temp < 14 else 0.7)
    rorhat_indeks = int(min(100, max(0, ((fugt_chok * 70) + (r_temp_score * 30)) * tørheds_straffe_faktor * 100)))

    c_temp_score = 1.0 if 15 <= aktuel_temp <= 25 else 0.6
    champignon_indeks = int(min(100, max(0, ((fugt_chok * 50) + (c_temp_score * 50)) * tørheds_straffe_faktor * 100)))

    samlet_score = int((kantarel_indeks + rorhat_indeks + champignon_indeks) / 3)

    return {
        "samlet": samlet_score,
        "kantarel": kantarel_indeks,
        "rorhat": rorhat_indeks,
        "champignon": champignon_indeks,
        "temp": aktuel_temp,
        "status": "OK"
    }

def generer_interaktivt_kort(resultater):
    html_inhold = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Svampe-Radar Horsens & Omegn</title>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background: #f4f4f9; }}
            header {{ background: #2c3e50; color: white; padding: 15px; text-align: center; }}
            #map {{ height: 80vh; width: 100%; }}
            .footer {{ text-align: center; padding: 10px; color: #7f8c8d; font-size: 12px; }}
        </style>
    </head>
    <body>
        <header>
            <h2>🍄 Danmarks Svampe-Radar ({datetime.now().strftime('%d-%m-%Y')})</h2>
        </header>
        <div id="map"></div>
        <div class="footer">Opdateret automatisk via Open-Meteo & GitHub Actions</div>

        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script>
            var map = L.map('map').setView([{HOME_LAT}, {HOME_LON}], 11);
            L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                maxZoom: 19,
                attribution: '© OpenStreetMap'
            }}).addTo(map);

            var hjem = L.marker([{HOME_LAT}, {HOME_LON}]).addTo(map);
            hjem.bindPopup("<b>🏡 Min Bopæl (Viborgvej 15)</b>");
    """

    for r in resultater:
        samlet_s = r['score'].get('samlet', 0)
        farve = "red" if samlet_s >= 75 else ("orange" if samlet_s >= 40 else "green")
        popup_tekst = (
            f"<b>{r['navn']}</b><br>"
            f"Afstand: {r['dist']} km<br>"
            f"<b>Samlet Boom: {samlet_s}%</b><br>"
            f"• Kantarel: {r['score'].get('kantarel', 0)}%<br>"
            f"• Rørhat: {r['score'].get('rorhat', 0)}%<br>"
            f"• Champignon: {r['score'].get('champignon', 0)}%<br>"
            f"Jordtemp: {r['score'].get('temp', 0.0):.1f}°C"
        )
        html_inhold += f"""
            L.circleMarker([{r['lat']}, {r['lon']}], {{
                color: '{farve}',
                radius: 10,
                fillOpacity: 0.8
            }}).addTo(map).bindPopup("{popup_tekst}");
        """

    html_inhold += """
        </script>
    </body>
    </html>
    """

    with open("svampe_kort.html", "w", encoding="utf-8") as f:
        f.write(html_inhold)
    print("Interaktivt kort genereret som 'svampe_kort.html'!")


# --- HOVEDPROGRAM ---
print("Starter avanceret svampe-analyse...")
resultater = []
alarm_tekster = []

for skov, info in SKOV_LOCATIONS.items():
    dist = beregn_afstand(HOME_LAT, HOME_LON, info["lat"], info["lon"])
    df = hent_miljo_data(info["lat"], info["lon"])
    score = analyser_svampe_forhold(df, info["jordtype"])
    
    resultater.append({
        "navn": skov, "lat": info["lat"], "lon": info["lon"], 
        "dist": dist, "score": score
    })

    print(f"🌲 {skov}: Samlet Boom {score['samlet']}% (Kantarel: {score['kantarel']}%, Rørhat: {score['rorhat']}%)")

    if score["samlet"] >= 75:
        alarm_tekster.append(
            f"🚨 *{skov}* ({dist} km væk)\n"
            f"   • Samlet Boom-indeks: *{score['samlet']}%*\n"
            f"   • 🟡 Kantarel-potentiale: {score['kantarel']}%\n"
            f"   • 🟤 Rørhat-potentiale: {score['rorhat']}%\n"
            f"   • ⚪ Champignon-potentiale: {score['champignon']}%\n"
            f"   _Jordtemp: {score['temp']:.1f}°C_"
        )

# Generer kortet uanset hvad
generer_interaktivt_kort(resultater)

# Send Telegram besked
dato_str = datetime.now().strftime('%d-%m-%Y')
if alarm_tekster:
    msg = f"🍄 *SVAMPE-ALARM FOR HORSENS* ({dato_str}) 🍄\n\nOptimum fundet i følgende skove:\n\n" + "\n\n".join(alarm_tekster)
    send_telegram_besked(msg)
    print("Alarm sendt til Telegram!")
else:
    status_msg = f"🍄 *SVAMPE-STATUS: HORSENS* ({dato_str})\n\nIngen zoner har ramt den kritiske 75%-tærskel i dag, men data og vejrudsigt er opdateret. Det interaktive kort er genereret!"
    send_telegram_besked(status_msg)
    print("Status-besked sendt til Telegram.")