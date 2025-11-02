import streamlit as st
import ee
import datetime
import folium
import json
from streamlit_folium import folium_static
from geopy.geocoders import Nominatim
import plotly.express as px

# --- GEE AUTENTIMINE (PARANDATUD) ---
st.set_page_config(page_title="Päikesepaneelide Tolmuanalüüs", layout="wide")

st.title("☀️ Päikesepaneelide Tolmu- ja Varjuanalüüs")
st.write("Sisesta aadress ja ajavahemik ning analüüs algab ~10 sekundi jooksul!")

if 'gee' in st.secrets:
    try:
        # Võtame teenusekonto info TOML-ist
        credentials_info = dict(st.secrets['gee'])

        # Loome teenusekonto mandaadid
        credentials = ee.ServiceAccountCredentials(
            email=credentials_info['client_email'],
            key_data=json.dumps(credentials_info)
        )

        # Initsialiseerime Earth Engine
        ee.Initialize(credentials)
        st.success("✅ Google Earth Engine ühendus on loodud!")
    except Exception as e:
        st.error(f"❌ GEE autentimine ebaõnnestus: {e}")
        st.stop()
else:
    st.error("⚠️ GEE Secrets puudub! Lisa [gee] sektsioon faili `.streamlit/secrets.toml`.")
    st.stop()


# --- SISENDVORM ---
address = st.text_input("📍 Aadress", "Calle del Sol, Almería, Spain")
col1, col2 = st.columns(2)
start_date = col1.date_input("Alguskuupäev", datetime.date(2023, 6, 1))
end_date = col2.date_input("Lõppkuupäev", datetime.date(2023, 8, 31))

# --- PÕHIANALÜÜS ---
if st.button("🔍 Analüüsi"):
    with st.spinner("Laen satelliidipilte ja analüüsin..."):
        geocoder = Nominatim(user_agent="solar_app")
        location = geocoder.geocode(address)

        if not location:
            st.error("❌ Aadressi ei leitud! Palun sisesta täpsem asukoht.")
            st.stop()

        lat, lon = location.latitude, location.longitude

        # --- KAART ---
        m = folium.Map(location=[lat, lon], zoom_start=18)
        folium.CircleMarker([lat, lon], radius=200, color="red").add_to(m)
        draw = folium.plugins.Draw(export=True)
        draw.add_to(m)
        folium_static(m, width=900, height=500)

        # --- EARTH ENGINE ANDMED ---
        point = ee.Geometry.Point([lon, lat])

        collection = (
            ee.ImageCollection('COPERNICUS/S2_SR')
            .filterBounds(point)
            .filterDate(str(start_date), str(end_date))
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
            .select(['B8', 'B4'])
        )

        # NDVI arvutus
        def calc_ndvi(img):
            ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return img.addBands(ndvi)

        ndvi_col = collection.map(calc_ndvi)

        # Vähendame NDVI väärtused keskmiseks ühe punkti ümber
        stats = ndvi_col.map(lambda img: img.reduceRegion(
            ee.Reducer.mean(), point, 10
        ).set('date', img.date().format('YYYY-MM-dd')))

        data = stats.getInfo()['features']

        dates, ndvi_vals = [], []
        for d in data:
            props = d['properties']
            if 'NDVI' in props and props['NDVI'] is not None:
                dates.append(props['date'])
                ndvi_vals.append(props['NDVI'])

        if not dates:
            st.warning("⚠️ Satelliidipilte ei leitud valitud perioodil. Proovi teist kuupäeva!")
            st.stop()

        # --- TOLMU INDEKS ---
        tolm = [max(0, (0.7 - ndvi) / 0.4 * 100) for ndvi in ndvi_vals]

        df = {"Kuupäev": dates, "NDVI": ndvi_vals, "Tolm %": tolm}
        fig = px.line(df, x="Kuupäev", y=["NDVI", "Tolm %"],
                      title="NDVI ja Tolmu trend ajas",
                      labels={"value": "Väärtus", "variable": "Näitajad"})
        st.plotly_chart(fig, use_container_width=True)

        # --- TULEMUS ---
        max_tolm = max(tolm)
        if max_tolm > 35:
            st.error(f"⚠️ Paneelid on tolmused! Hinnanguline määr: {max_tolm:.1f}% – soovitame puhastada.")
            st.code("E-kiri saadetakse, kui Brevo integratsioon on valmis.")
        else:
            st.success("✅ Paneelid näivad olevat puhtad – tolmu mõju alla 35%.")

st.caption("🛰️ Andmed: Copernicus Sentinel-2 (via Google Earth Engine)")
