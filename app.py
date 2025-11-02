import streamlit as st
import ee, datetime, folium, json
from streamlit_folium import folium_static
from geopy.geocoders import Nominatim
import plotly.express as px

# --- GEE AUTENTIMINE SECRETS'IGA ---
if 'gee' in st.secrets:
    credentials_info = st.secrets['gee']
    credentials = ee.ServiceAccountCredentials(
        email=credentials_info['client_email'],
        key_data=json.dumps(credentials_info)
    )
    ee.Initialize(credentials)
    st.success("✅ GEE ühendatud teenusekontoga!")
else:
    st.error("⚠️ GEE Secrets puudub! Lisa [gee] TOML.")
    st.stop()

st.title("☀️ Päikesepaneelide Tolmu- ja Varjuanalüüs")
st.write("Sisesta aadress + kuupäev → analüüs 10s!")

address = st.text_input("Aadress", "Calle del Sol, Almería, Spain")
col1, col2 = st.columns(2)
start_date = col1.date_input("Algus", datetime.date(2023, 6, 1))
end_date = col2.date_input("Lõpp", datetime.date(2023, 8, 31))

if st.button("🔍 Analüüsi"):
    with st.spinner("Laen satelliidipilte..."):
        geocoder = Nominatim(user_agent="solar_app")
        location = geocoder.geocode(address)
        if not location:
            st.error("Aadressi ei leitud!")
            st.stop()
        lat, lon = location.latitude, location.longitude

        m = folium.Map(location=[lat, lon], zoom_start=18)
        folium.CircleMarker([lat, lon], radius=200, color="red").add_to(m)
        draw = folium.plugins.Draw(export=True)
        draw.add_to(m)
        folium_static(m)

        point = ee.Geometry.Point([lon, lat])
        collection = (ee.ImageCollection('COPERNICUS/S2_SR')
                      .filterBounds(point)
                      .filterDate(str(start_date), str(end_date))
                      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                      .select(['B8', 'B4']))

        def calc_ndvi(img):
            ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return img.addBands(ndvi)

        ndvi_col = collection.map(calc_ndvi)
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
            st.warning("Pilte ei leitud! Proovi teist perioodi.")
            st.stop()

        tolm = [max(0, (0.7 - ndvi) / 0.4 * 100) for ndvi in ndvi_vals]

        df = {"Kuupäev": dates, "NDVI": ndvi_vals, "Tolm %": tolm}
        fig = px.line(df, x="Kuupäev", y=["NDVI", "Tolm %"], title="NDVI & Tolm")
        st.plotly_chart(fig)

        if max(tolm) > 35:
            st.error(f"⚠️ Tolmune! {max(tolm):.1f}% – Puhasta!")
            st.code("E-kiri saadetakse, kui Brevo valmis!")
        else:
            st.success("✅ Paneelid puhtad!")
