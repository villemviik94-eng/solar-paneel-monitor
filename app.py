import streamlit as st
import ee
import datetime
import folium
import json
from streamlit_folium import folium_static
from geopy.geocoders import Nominatim
import plotly.express as px
from skyfield.api import load, wgs84
from skyfield.almanac import find_discrete, sunrise_sunset
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
import numpy as np

# --- PAGE CONFIG ---
st.set_page_config(page_title="Päikesepaneelide Tolmu- ja Varjuanalüüs", layout="wide")
st.title("☀️ Päikesepaneelide Tolmu- ja Varjuanalüüs")
st.markdown("Sisesta aadress → analüüs satelliitpiltidest + varjud + teavitus!")

# --- GEE AUTENTIMINE (secrets.toml) ---
if 'gee' in st.secrets:
    try:
        credentials_info = dict(st.secrets['gee'])
        credentials = ee.ServiceAccountCredentials(
            email=credentials_info['client_email'],
            key_data=json.dumps(credentials_info)
        )
        ee.Initialize(credentials)
        st.success("✅ Google Earth Engine ühendus loodud!")
    except Exception as e:
        st.error(f"❌ GEE viga: {e}")
        st.stop()
else:
    st.error("⚠️ Lisa `.streamlit/secrets.toml` faili [gee] sektsioon!")
    st.stop()

# --- SISENDVORM ---
with st.form("input_form"):
    address = st.text_input("📍 Aadress", "Tallinn, Harju maakond, Eesti")
    col1, col2 = st.columns(2)
    start_date = col1.date_input("Alguskuupäev", datetime.date.today() - datetime.timedelta(days=60))
    end_date = col2.date_input("Lõppkuupäev", datetime.date.today())
    email_recipient = st.text_input("📧 Teavituse e-post (valikuline)", "")
    submitted = st.form_submit_button("🔍 Analüüsi")

if submitted:
    if (end_date - start_date).days > 90:
        st.warning("⚠️ Vali kuni 3 kuud korraga.")
        st.stop()

    with st.spinner("Laen satelliidipilte ja analüüsin... 🛰️"):
        # --- GEOKOODEERIMINE ---
        geocoder = Nominatim(user_agent="solar_monitor_app")
        location = geocoder.geocode(address)
        if not location:
            st.error("❌ Aadressi ei leitud!")
            st.stop()
        lat, lon = location.latitude, location.longitude

        # --- KAART ---
        m = folium.Map(location=[lat, lon], zoom_start=18, tiles="OpenStreetMap")
        folium.Circle(
            location=[lat, lon],
            radius=200,
            color="red",
            weight=2,
            fill=False,
            popup="200m analüüsiala"
        ).add_to(m)

        # --- EARTH ENGINE ---
        point = ee.Geometry.Point([lon, lat])
        buffer = point.buffer(200)

        collection = (
            ee.ImageCollection('COPERNICUS/S2_SR')
            .filterBounds(buffer)
            .filterDate(str(start_date), str(end_date))
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
            .select(['B8', 'B4'])
        )

        def calc_ndvi(img):
            ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return img.addBands(ndvi)

        ndvi_col = collection.map(calc_ndvi)

        # --- KESKMINE NDVI PILT KAARDIL ---
        mean_ndvi_image = ndvi_col.mean().select('NDVI').clip(buffer)
        ndvi_vis = {
            'min': 0, 'max': 0.8,
            'palette': ['#8B0000', '#FF4500', '#FFD700', '#ADFF2F', '#228B22']
        }
        map_id = mean_ndvi_image.getMapId(ndvi_vis)
        folium.TileLayer(
            tiles=map_id['tile_fetcher'].url_format,
            attr='Google Earth Engine',
            name='NDVI (punane = tolmune, roheline = puhas)',
            overlay=True,
            control=True
        ).add_to(m)
        folium.LayerControl().add_to(m)

        # --- NDVI ANDMED ---
        def add_mean(img):
            mean = img.reduceRegion(ee.Reducer.mean(), buffer, 10).get('NDVI')
            return img.set('mean_ndvi', mean).set('date', img.date().format('YYYY-MM-dd'))

        stats = ndvi_col.map(add_mean)
        info = stats.getInfo()

        dates = []
        ndvi_vals = []
        for feature in info['features']:
            props = feature['properties']
            if 'mean_ndvi' in props and props['mean_ndvi'] is not None:
                dates.append(props['date'])
                ndvi_vals.append(props['mean_ndvi'])

        if not dates:
            st.warning("⚠️ Satelliidipilte ei leitud (pilved või andmed puuduvad).")
            st.stop()

        # --- TOLMU INDEKS ---
        tolm = [max(0, min(100, (0.7 - ndvi) / 0.4 * 100)) for ndvi in ndvi_vals]
        avg_ndvi = np.mean(ndvi_vals)
        max_tolm = max(tolm)

        # --- VARJUDE ANALÜÜS ---
        @st.cache_data
        def calculate_sunlight(lat, lon, date):
            ts = load.timescale()
            t0 = ts.utc(date.year, date.month, date.day)
            t1 = ts.utc(date.year, date.month, date.day + 1)
            eph = load('de421.bsp')
            site = wgs84.latlon(lat, lon)
            f = sunrise_sunset(eph, site)
            times, events = find_discrete(t0, t1, f)
            sunrise = next((t for t, e in zip(times, events) if e == 1), None)
            sunset = next((t for t, e in zip(times, events) if e == 0), None)
            if sunrise and sunset:
                hours = (sunset.utc_datetime() - sunrise.utc_datetime()).total_seconds() / 3600
                effective = hours * 0.75  # 25% varju (puud, hooned)
                return round(hours, 1), round(effective, 1)
            return 0, 0

        total_sun, effective_sun = calculate_sunlight(lat, lon, datetime.date.today())

        # --- GRAAFIK ---
        df = {
            "Kuupäev": dates,
            "NDVI": ndvi_vals,
            "Tolm %": tolm
        }
        fig = px.line(
            df, x="Kuupäev", y=["NDVI", "Tolm %"],
            title="NDVI ja Tolmu trend ajas",
            labels={"value": "Väärtus", "variable": "Näitaja"},
            color_discrete_sequence=['#228B22', '#FF4500']
        )
        fig.update_layout(legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01))

        # --- TULEMUSED ---
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Maks. tolm", f"{max_tolm:.1f}%", delta=f"{max_tolm-35:.1f}%")
        with col2:
            st.metric("NDVI keskmine", f"{avg_ndvi:.3f}")
        with col3:
            st.metric("Efektiivne päike", f"{effective_sun}h", delta="-25% varju")

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("### 🗺️ Satelliitpilt + NDVI (200m ala)")
        folium_static(m, width=900, height=500)

        # --- TEAVITUS ---
        if max_tolm > 35 and email_recipient:
            if st.button("📩 Saada teavitus"):
                try:
                    sender = st.secrets["email"]["sender"]
                    password = st.secrets["email"]["password"]

                    msg = MIMEMultipart()
                    msg["From"] = sender
                    msg["To"] = email_recipient
                    msg["Subject"] = f"⚠️ Päikesepaneelid tolmused: {address}"

                    body = f"""
                    AUTOMAATNE TEAVITUS

                    Aadress: {address}
                    Maksimaalne tolm: {max_tolm:.1f}%
                    NDVI keskmine: {avg_ndvi:.3f}
                    Efektiivne päikeseaeg: {effective_sun} tundi

                    Soovitus: Puhasta paneelid!

                    Analüüs: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}
                    """
                    msg.attach(MIMEText(body, "plain"))

                    server = smtplib.SMTP("smtp-relay.brevo.com" if "brevo" in sender else "smtp.gmail.com", 587)
                    server.starttls()
                    server.login(sender, password)
                    server.send_message(msg)
                    server.quit()
                    st.success(f"Teavitus saadetud: {email_recipient}")
                except Exception as e:
                    st.error(f"E-posti viga: {e}")
        elif max_tolm > 35:
            st.warning("Sisesta e-posti aadress, et saada teavitus!")

st.caption("🛰️ Andmed: Copernicus Sentinel-2 | Google Earth Engine | Skyfield | Streamlit")
