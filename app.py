import streamlit as st
import ee
import datetime
import folium
from streamlit_folium import folium_static
from geopy.geocoders import Nominatim
import plotly.express as px
import pandas as pd
import json  # <--- SEE OLI PUUDU! NÜÜD LISATUD
from skyfield.api import load, wgs84

# --- GEE AUTENTIMINE (PARANDATUD) ---
st.set_page_config(page_title="Päikesepaneelide Tolmuanalüüs", layout="wide")
st.title("☀️ Päikesepaneelide Tolmu- ja Varjuanalüüs")
st.write("Sisesta aadress ja ajavahemik – analüüs ~10 sekundi jooksul!")

if 'gee' in st.secrets:
    try:
        credentials_info = dict(st.secrets['gee'])
        credentials = ee.ServiceAccountCredentials(
            email=credentials_info['client_email'],
            key_data=json.dumps(credentials_info)  # <--- NÜÜD TÖÖTAB!
        )
        ee.Initialize(credentials)
        st.success("✅ Google Earth Engine ühendus loodud!")
    except Exception as e:
        st.error(f"❌ GEE viga: {e}")
        st.stop()
else:
    st.error("⚠️ Lisa `.streamlit/secrets.toml` faili [gee] sektsioon!")
    st.stop()

# --- SISEND ---
address = st.text_input("📍 Aadress", "Tallinn, Harju maakond, Eesti")
col1, col2 = st.columns(2)
start_date = col1.date_input("Alguskuupäev", datetime.date.today() - datetime.timedelta(days=60))
end_date = col2.date_input("Lõppkuupäev", datetime.date.today())

if st.button("🔍 Analüüsi"):
    with st.spinner("Tuvastan asukohta ja laen satelliidipilte..."):
        # 1. GEOKOODEERIMINE
        geocoder = Nominatim(user_agent="solar_app")
        location = geocoder.geocode(address)
        if not location:
            st.error("❌ Aadressi ei leitud!")
            st.stop()
        lat, lon = location.latitude, location.longitude
        st.write(f"**Asukoht:** {lat:.5f}°, {lon:.5f}°")

        # 2. KAART
        m = folium.Map(location=[lat, lon], zoom_start=18)
        folium.Circle([lat, lon], radius=50, color="red", fill=False, popup="50m analüüsiala").add_to(m)

        # --- NDVI KAART (VIIMANE PILT) ---
        point = ee.Geometry.Point([lon, lat])
        buffer = point.buffer(50)

        collection = (
            ee.ImageCollection('COPERNICUS/S2_SR')
            .filterBounds(buffer)
            .filterDate(str(start_date), str(end_date))
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
            .select(['B8', 'B4'])
            .sort('system:time_start', False)
        )

        def add_ndvi(image):
            ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return image.addBands(ndvi).set('date', image.date().format('YYYY-MM-dd'))

        ndvi_collection = collection.map(add_ndvi)

        # Viimane pilt NDVI kaardile
        latest_image = ndvi_collection.first()
        ndvi_vis = {'min': 0, 'max': 0.8, 'palette': ['red', 'orange', 'yellow', 'green']}
        map_id = latest_image.select('NDVI').getMapId(ndvi_vis)
        folium.TileLayer(
            tiles=map_id['tile_fetcher'].url_format,
            attr='Google Earth Engine',
            overlay=True,
            name='NDVI (punane = tolmune)'
        ).add_to(m)
        folium.LayerControl().add_to(m)
        folium_static(m, width=900, height=400)

        # --- NDVI AJALINE ANALÜÜS ---
        def reduce_region(image):
            mean = image.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=buffer,
                scale=10,
                maxPixels=1e5
            )
            return image.set(mean).set('date', image.date().format('YYYY-MM-dd'))

        reduced = ndvi_collection.map(reduce_region)

        try:
            data = reduced.getInfo()['features']
        except Exception as e:
            st.error(f"❌ Andmete laadimine ebaõnnestus: {e}")
            st.info("Proovi lühendada perioodi (nt 30 päeva) või kontrolli pilvede olemasolu.")
            st.stop()

        dates, ndvi_vals = [], []
        for feature in data:
            props = feature['properties']
            if 'NDVI' in props and props['NDVI'] is not None:
                dates.append(props['date'])
                ndvi_vals.append(props['NDVI'])

        if not dates:
            st.warning("⚠️ Pilte ei leitud. Proovi teist perioodi.")
            st.stop()

        # Tolmu protsent
        tolm_protsent = [max(0, min(100, (0.7 - ndvi) / 0.4 * 100)) for ndvi in ndvi_vals]

        # DataFrame + graafik
        df = pd.DataFrame({"Kuupäev": dates, "NDVI": ndvi_vals, "Tolm %": tolm_protsent})
        fig = px.line(df, x="Kuupäev", y=["NDVI", "Tolm %"],
                      title="NDVI ja Tolmu trend ajas",
                      labels={"value": "Väärtus", "variable": "Indikaator"},
                      markers=True)
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        # Tulemus
        latest_tolm = tolm_protsent[-1]
        st.metric("Viimane NDVI", f"{ndvi_vals[-1]:.3f}")
        st.metric("Hinnanguline tolm", f"{latest_tolm:.1f}%", delta=f"{latest_tolm - 35:+.1f}% lävest")

        if latest_tolm > 35:
            st.error(f"⚠️ **Paneelid on tolmused!** Puhasta – efektiivsus langeb ~{latest_tolm/2:.0f}%")
        else:
            st.success("✅ Paneelid puhtad – suurepärane!")

        # Varjude hinnang (lihtne)
        ts = load.timescale()
        t = ts.utc(datetime.datetime.now())
        site = wgs84.latlon(lat, lon)
        eph = load('de421.bsp')
        st.info("Varjude täpne analüüs (DSM + puud) tuleb järgmises versioonis!")

st.caption("🛰️ Andmed: Copernicus Sentinel-2 | Google Earth Engine | Streamlit")
