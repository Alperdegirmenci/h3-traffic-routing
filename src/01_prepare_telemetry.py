import os
import glob
import json
import h3
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

class H3DataPipeline:
    def __init__(self, raw_data_dir, output_parquet, h3_resolution=8):
        """
        Mekansal veri işleme boru hattını (Cloud-Native Spatial Pipeline) başlatır.
        """
        self.raw_data_dir = raw_data_dir
        self.output_parquet = output_parquet
        self.res = h3_resolution
        
        # Konya İl Merkezi Yaklaşık Koordinat Sınırları (Bounding Box Filtresi)
        self.konya_bounds = {
            "min_lat": 37.50, "max_lat": 38.30,
            "min_lon": 32.20, "max_lon": 32.80
        }

    def parse_raw_gps_files(self):
        """
        data/raw/GPS.DATA altındaki tüm .txt dosyalarını dinamik olarak tarar, 
        parse eder ve tek bir entegre DataFrame haline getirir.
        """
        search_pattern = os.path.join(self.raw_data_dir, "*.txt")
        file_list = glob.glob(search_pattern)
        
        if not file_list:
            print(f"[WARNING] '{self.raw_data_dir}' dizininde işlenecek .txt dosyası bulunamadı!")
            return pd.DataFrame()
            
        print(f"[INFO] Toplam {len(file_list)} adet ham veri dosyası bulundu. İşleniyor...")
        
        all_data = []
        columns = [
            'ayrilmis_alan', 'arac_id', 'zaman_damgasi', 'yukseklik', 
            'enlem', 'boylam', 'hiz', 'yon', 'tur_kaynagi', 'arac_turu'
        ]
        
        for file_path in file_list:
            try:
                # Semicolon (Anlam ayrıştırıcı ;) ile ayrılmış Arvento ham verisini okuma
                df = pd.read_csv(file_path, sep=';', header=None, names=columns, dtype={'arac_id': str})
                all_data.append(df)
            except Exception as e:
                print(f"[ERROR] Dosya okunurken hata oluştu ({os.path.basename(file_path)}): {str(e)}")
                
        if not all_data:
            return pd.DataFrame()
            
        combined_df = pd.concat(all_data, ignore_index=True)
        print(f"[SUCCESS] Toplam {len(combined_df)} satır ham GPS verisi bellek üzerine alındı.")
        return combined_df

    def process_and_save_geoparquet(self, df):
        """
        Veriyi temizler, float tipine zorlar, Konya sınırlarına göre filtreler 
        ve H3 altıgen indekslerini hatasız şekilde hesaplar.
        """
        if df.empty:
            print("[ERROR] İşlenecek veri kümesi boş.")
            return pd.DataFrame()

        print("[INFO] Veri temizleme ve tip dönüşüm işlemleri başlatıldı...")
        
        # --- TIP GÜVENLİĞİ VE VERİ TEMİZLEME ADIMI ---
        # Enlem, boylam ve hız kolonlarını zorunlu olarak float tipine çeviriyoruz.
        # Sayıya çevrilemeyen bozuk karakterler (errors='coerce') otomatik olarak NaN yapılır.
        df['enlem'] = pd.to_numeric(df['enlem'], errors='coerce')
        df['boylam'] = pd.to_numeric(df['boylam'], errors='coerce')
        df['hiz'] = pd.to_numeric(df['hiz'], errors='coerce')

        # Koordinat bilgisi eksik, boş veya bozuk olan tüm satırları h3 hatası almamak için eliyoruz
        df = df.dropna(subset=['enlem', 'boylam'])
        
        # 1. Aşama: Konya Bounding Box Coğrafi Filtrelemesi
        df_filtered = df[
            (df['enlem'] >= self.konya_bounds['min_lat']) & (df['enlem'] <= self.konya_bounds['max_lat']) &
            (df['boylam'] >= self.konya_bounds['min_lon']) & (df['boylam'] <= self.konya_bounds['max_lon'])
        ].copy()
        
        print(f"[INFO] Konya coğrafi sınırları filtrelemesi sonrası kalan temiz satır sayısı: {len(df_filtered)}")
        
        if df_filtered.empty:
            print("[WARNING] Konya sınırları içerisinde geçerli araç noktası saptanmadı. Filtre sınırlarını kontrol edin.")
            return pd.DataFrame()

        # 2. Aşama: Matematiksel H3 Altıgen İndekslerinin Üretilmesi (H3 v4 Standardı: latlng_to_cell)
        print("[INFO] H3 v4 standartlarında altıgen hücre indeksleri hesaplanıyor...")
        df_filtered['h3_index'] = df_filtered.apply(
            lambda row: h3.latlng_to_cell(float(row['enlem']), float(row['boylam']), self.res), axis=1
        )

        # 3. Aşama: GeoDataFrame ve Sütun Tabanlı GeoParquet Dönüşümü
        geometry = [Point(xy) for xy in zip(df_filtered['boylam'], df_filtered['enlem'])]
        gdf = gpd.GeoDataFrame(df_filtered, geometry=geometry, crs="EPSG:4326")
        
        # Parquet dizinini otomatik oluşturma ve kaydetme
        os.makedirs(os.path.dirname(self.output_parquet), exist_ok=True)
        gdf.to_parquet(self.output_parquet, compression='snappy')
        print(f"[SUCCESS] Büyük mekansal veri seti GeoParquet olarak kaydedildi: {self.output_parquet}")
        
        return gdf

    def generate_mapbox_html(self, gdf, output_html_path):
        """
        H3 hücre yoğunluklarını hesaplar ve TAMAMEN AÇIK KAYNAKLI, 
        API Key gerektirmeyen MapLibre GL JS ve OSM altyapılı haritayı inşa eder.
        """
        if gdf.empty:
            print("[ERROR] Harita üretimi için geçerli veri seti mevcut değil.")
            return

        print("[INFO] API Key gerektirmeyen MapLibre + OSM haritası hazırlanıyor...")
        
        # H3 hücre bazlı araç yoğunluğu ve ortalama hız analitiği
        h3_summary = gdf.groupby('h3_index').agg(
            arac_sayisi=('arac_id', 'count'),
            ort_hiz=('hiz', 'mean')
        ).reset_index()

        # H3 indekslerini MapLibre'nin anlayacağı GeoJSON Poligon formatına çevirme
        features = []
        for _, row in h3_summary.iterrows():
            h3_id = row['h3_index']
            
            # 1. H3 v4 standardında köşe koordinatlarını çekme: ((lat, lon), (lat, lon)...) döner
            vertices_raw = h3.cell_to_boundary(h3_id)
            
            # 2. MapLibre/GeoJSON için koordinat yönelimini [Boylam, Enlem] olarak normalize etme
            vertices = [[coord[1], coord[0]] for coord in vertices_raw]
            
            # 3. GeoJSON standardı için poligon zincirini kapatma
            vertices_closed = vertices + [vertices[0]]
            
            feature = {
                "type": "Feature",
                "properties": {
                    "h3_index": str(h3_id),
                    "arac_sayisi": int(row['arac_sayisi']),
                    "ortalama_hiz": round(float(row['ort_hiz']), 2)
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [vertices_closed]
                }
            }
            features.append(feature)

        geojson_data = {
            "type": "FeatureCollection",
            "features": features
        }

        # %100 AÇIK KAYNAKLI MAPLIBRE GL JS + OSM ŞABLONU
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Konya H3 Trafik Hotspot Analizi (MapLibre)</title>
            <meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
            <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet">
            <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
            <style>
                body {{ margin: 0; padding: 0; font-family: 'Helvetica Neue', Arial, sans-serif; }}
                #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
                .legend {{
                    position: absolute; background: rgba(255, 255, 255, 0.95); padding: 15px;
                    border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);
                    bottom: 30px; right: 20px; z-index: 1; font-size: 12px; line-height: 18px; color: #333333;
                    border: 1px solid #ccc;
                }}
                .legend-key {{ display: inline-block; width: 20px; height: 10px; margin-right: 5px; border: 1px solid #888; }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <div class="legend" id="legend">
                <h4 style="margin-top:0; margin-bottom:10px;">Araç Yoğunluğu (Hotspot)</h4>
                <div><span class="legend-key" style="background: #f7fcb9;"></span>1 - 2 Araç</div>
                <div><span class="legend-key" style="background: #addd8e;"></span>3 - 5 Araç</div>
                <div><span class="legend-key" style="background: #31a354;"></span>6 - 10 Araç</div>
                <div><span class="legend-key" style="background: #e34a33;"></span>10+ Araç (Kritik Yoğunluk)</div>
            </div>

            <script>
                // MapLibre nesnesi başlatılır (Hiçbir token/anahtar parametresi gerektirmez)
                const map = new maplibregl.Map({{
                    container: 'map',
                    style: {{
                        'version': 8,
                        'sources': {{}},
                        'layers': []
                    }},
                    center: [32.492, 37.872], // Konya Merkez
                    zoom: 12
                }});

                const geojsonData = {json.dumps(geojson_data)};

                map.on('load', () => {{
                    
                    // --- AÇIK KAYNAK OPENSTREETMAP RASTER KATMANI ENJEKSİYONU ---
                    map.addSource('openstreetmap-tiles', {{
                        'type': 'raster',
                        'tiles': [
                            'https://a.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
                            'https://b.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
                            'https://c.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'
                        ],
                        'tileSize': 256,
                        'attribution': '&copy; OpenStreetMap contributors'
                    }});

                    map.addLayer({{
                        'id': 'openstreetmap-layer',
                        'type': 'raster',
                        'source': 'openstreetmap-tiles',
                        'minzoom': 0,
                        'maxzoom': 19
                    }});

                    // --- H3 GEOMETRİ KAYNAĞININ ENJEKTE EDİLMESİ ---
                    map.addSource('h3-traffic', {{
                        'type': 'geojson',
                        'data': geojsonData
                    }});

                    // Altıgen Dolgu Katmanı (Choropleth Map)
                    map.addLayer({{
                        'id': 'h3-layer-fill',
                        'type': 'fill',
                        'source': 'h3-traffic',
                        'paint': {{
                            'fill-color': [
                                'interpolate', ['linear'], ['get', 'arac_sayisi'],
                                1, '#f7fcb9',
                                3, '#addd8e',
                                6, '#31a354',
                                10, '#e34a33'
                            ],
                            'fill-opacity': 0.55
                        }}
                    }});

                    // Altıgen Sınır Çizgileri
                    map.addLayer({{
                        'id': 'h3-layer-outline',
                        'type': 'line',
                        'source': 'h3-traffic',
                        'paint': {{
                            'line-color': '#222222',
                            'line-width': 1.2,
                            'line-opacity': 0.4
                        }}
                    }});

                    // İnteraktif Popup Tıklama Olayı
                    map.on('click', 'h3-layer-fill', (e) => {{
                        const props = e.features[0].properties;
                        new maplibregl.Popup()
                            .setLngLat(e.lngLat)
                            .setHTML(`
                                <div style="color:#222; font-size:12px; line-height:16px;">
                                    <strong>H3 İndeks:</strong> \${{props.h3_index}}<br>
                                    <strong>Araç Sayısı:</strong> \${{props.arac_sayisi}}<br>
                                    <strong>Ort. Hız:</strong> \${{props.ortalama_hiz}} km/s
                                </div>
                            `)
                            .addTo(map);
                    }});

                    map.on('mouseenter', 'h3-layer-fill', () => map.getCanvas().style.cursor = 'pointer');
                    map.on('mouseleave', 'h3-layer-fill', () => map.getCanvas().style.cursor = '');
                }});
            </script>
        </body>
        </html>
        """
        
        with open(output_html_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        print(f"[SUCCESS] %100 Özgür Açık Kaynak MapLibre + OSM Destekli Harita Üretildi: {output_html_path}")

if __name__ == "__main__":
    # Proje dizin yapısına göre dosya yollarının konfigüre edilmesi
    pipeline = H3DataPipeline(
        raw_data_dir=os.path.join("data", "raw", "GPS.DATA"),
        output_parquet=os.path.join("data", "processed", "konya_traffic.parquet"),
        h3_resolution=8 # Şehir içi makro yoğunluk analizleri için ideal altıgen yarıçapı
    )
    
    # 1. Adım: data/raw/GPS.DATA altındaki txt dosyalarını tara ve parse et
    raw_df = pipeline.parse_raw_gps_files()
    
    # 2. Adım: Tip Temizliği + Konya filtrelemesi + H3 v4 İndeksleme + Sıkıştırılmış GeoParquet Kaydı
    processed_gdf = pipeline.process_and_save_geoparquet(raw_df)
    
    # 3. Adım: MapLibre GL JS tabanlı interaktif index.html haritasını oluştur
    pipeline.generate_mapbox_html(processed_gdf, "index.html")