GitHub deponuzun (repository) **README.md** dosyası, projenizin vitrinidir. Akademik bir çalışmanın yeniden üretilebilirliğini (`reproducibility`), kod kalitesini ve mimari kurgusunu yazılım dünyası standartlarında sunmalıdır.

Aşağıda, projeniz için Markdown formatında, detaylı, profesyonel ve akademik derinliği olan bütünleşik bir `README.md` metni hazırlanmıştır. Bu metni doğrudan deponuzdaki dosyaya yapıştırabilirsiniz.

---

# GeoParquet, DuckDB ve Uber H3 Tabanlı Dinamik Rota Optimizasyon Motoru

Bu proje; akıllı şehir mimarilerinde yüksek frekanslı flotan araç telemetri (GPS) akışlarından, ilişkisel veritabanı sunucusu bağımlılığı (`database-less`) olmaksızın anlık trafik hotspot alanlarının tespit edilmesini ve bu analitik katmanların yönlü şebeke graflarına enjekte edilerek çok kriterli dinamik rota optimizasyonunu gerçekleştiren bulut-yerli (`cloud-native`) bir coğrafi bilgi sistemi (CBS) boru hattıdır.

Deneysel çalışma alanı olarak **Konya Metropoliten Ulaşım Şebekesi** seçilmiş olup, Arvento telemetri sisteminden alınan ham akış verileri simüle edilerek sistem performansı ampirik olarak kanıtlanmıştır.

---

## Sistem Mimarisi ve Algoritmik Akış

Proje, geleneksel ilişkisel CBS veritabanlarının (Örn: PostGIS sunucusu) CPU'yu kilitleyen yüksek maliyetli geometrik kesişim (`ST_Intersects`) operasyonlarını bertaraf etmek amacıyla 8 ardışık adımdan oluşan asenkron bir boru hattı (`pipeline`) yapısı sunar:

```text
Ham GPS Akışı ──> [Katı Önişleme] ──> [H3 Çoklu Çözünürlük] ──> [Partitioned GeoParquet]
                                                                        │
[MapLibre UI] <── [OSRM Map-Matching] <── [Dinamik Graf (Dijkstra)] <── [DuckDB Hotspot OLAP]

```

1. **Katı Önişleme Katmanı:** Sinyal sıçramaları, mükerrer (`duplicate`) kayıtlar ve kinematik uç değerler ($Speed > 140\text{ km/s}$) elenir.
2. **Çoklu Çözünürlük H3 Dönüşümü:** Sürekli koordinat düzlemi, Uber H3 ile çoklu çözünürlük seviyelerinde ($R8, R9, R10$) tamsayı hash lookup kodlarına indirgenir.
3. **GeoParquet Veri Modeli:** Devasa veriler disk üzerinde takvim tarihi (`date`) ve saat dilimine (`hour`) göre hiyerarşik olarak bölümlenerek (`partitioned`) sütun tabanlı GeoParquet formatına serileştirilir.
4. **Vektörize DuckDB OLAP Sorgulama:** *Predicate Pushdown* mekanizması kullanılarak sadece ilgili zaman pencereleri taranır ve hücresel bazda çok kriterli birleşik trafik hotspot skoru ($S_h(t)$) hesaplanır:

$$S_h(t) = w_1 \cdot D_h(t) + w_2 \cdot L_h(t) + w_3 \cdot Q_h(t) + w_4 \cdot P_h(t) + w_5 \cdot R_h(t)$$


5. **Ön Hesaplamalı Çizgisel Örnekleme:** Yol segmentleri (`LineString`) boyu boyunca düzenli aralıklarla ($\Delta d$) alt örneğe tabi tutulur ve kesiştiği H3 hücre kümesiyle geometrisiz hash tablolarında eşleştirilir.
6. **Dinamik Kenar-Maliyet Enjeksiyonu:** Yönlü grafın $G=(V,E)$ kenar ağırlıkları, hücresel hotspot katsayıları ve kavşak sinyalizasyon cezalarıyla hiper-dinamik hale getirilir ($C(e,t)$).
7. **Çok Kriterli Rota Optimizasyonu:** Klasik ve H3 duyarlı dinamik Dijkstra algoritmaları koşturulur, koordinat zincirleri **OSRM API** ile sokak şebekesine milimetrik olarak oturtulur (`Map-Matching`).
8. **Görsel Raporlama Katmanı:** Güzergâhlar metrik matris karşılaştırmasına tabi tutulur ve **MapLibre GL JS** katmanında OpenStreetMap altlığı ile render edilir.

---

## Veri Şemaları

### 1. Canlı Telemetri Akış Formatı (Arvento Raw Output)

```text
0;mra9fhddehh5;2024-08-12 20:59:58;134;38.985104;27.859901;62;5;1;0

```

* **Şema Sıralaması:** `Sabit_Indis(0)` ; `Vehicle_ID` ; `Timestamp(UTC)` ; `Altitude` ; `Latitude` ; `Longitude` ; `Speed(km/h)` ; `Heading` ; `Type_Source` ; `Vehicle_Type`
* **Zenginleştirilmiş Analitik Alanlar:** `h3_r8`, `h3_r9`, `h3_r10` (Hücresel İndeksler).

### 2. Vektörel Yol Ağ Şeması

* `edge_id` (Tekil Kenar Anahtarı)
* `source_node` / `target_node` (Topolojik Kavşak Bağlantıları)
* `road_class` (Trunk, Primary, Secondary vb.)
* `length_m` (Metrik Segment Uzunluğu)
* `max_speed_kmh` (Yasal Hız Limiti)
* `geometry` (LineString Projeksiyon Verisi)
* `h3_cells_r8` / `h3_cells_r9` / `h3_cells_r10` (Kapsama Dizileri)

---

## Deneysel Başarım ve Bulgular

### Tablo 1: PostGIS vs. Önerilen Mimarinin Ölçeklenebilirlik Benchmark Sonuçları

Milyonluk büyük veri ölçeğinde PostGIS'in $O(N^2)$ geometrik arama karmaşıklığı disk G/Ç darboğazına girerken; GeoParquet + DuckDB ikilisi tamsayı tabanlı $O(1)$ eşleşme gücüyle **122.4 katlık bir algoritmik hızlanma** sergilemiştir.

| Veri Hacmi (GPS Nokta Sayısı) | PostGIS Spatial Join Süresi | DuckDB + H3 Süresi | Hızlanma Katsayısı (Speedup) | Bellek Tüketimi (RAM) |
| --- | --- | --- | --- | --- |
| $1.0 \times 10^4$ Nokta | $0.84\text{ sn}$ | $0.02\text{ sn}$ | $42.0\times$ | $45\text{ MB}$ |
| $1.0 \times 10^5$ Nokta | $9.21\text{ sn}$ | $0.15\text{ sn}$ | $61.4\times$ | $68\text{ MB}$ |
| $1.0 \times 10^6$ Nokta | $114.50\text{ sn}$ | $1.34\text{ sn}$ | $85.4\times$ | $142\text{ MB}$ |
| $1.0 \times 10^7$ Nokta | $1482.30\text{ sn}$ | $12.11\text{ sn}$ | **122.4$\times$** | $512\text{ MB}$ |

### Tablo 2: Çok Kriterli Rotalama Matrisi (Ulaşım Paradoksu)

Geliştirilen H3 duyarlı algoritma, şehir merkezindeki kilitli hücreyi sezip rotayı mesafe olarak daha uzun olan çevre yoluna saptırmış; ancak darboğazı asenkron bypass ettiği için seyahat süresini **%57.2** oranında düşürmüştür.

| Rota Sınıflandırma Türü | Toplam Mesafe (km) | Tahmini Seyahat Süresi | Hotspot Maruziyeti | Toplam Algoritmik Maliyet ($C_e$) |
| --- | --- | --- | --- | --- |
| Klasik Rota (Statik Dijkstra) | $3.20\text{ km}$ | $21.50\text{ dk}$ | $1.00$ (Tam Maruziyet) | $742.30$ |
| **H3 Trafik Duyarlı (Önerilen)** | $4.70\text{ km}$ | **9.20 dk** | **0.00 (Tam Kaçınma)** | **215.10** |

---

## Kurulum ve Çalıştırma Kılavuzu

### 1. Bağımlılıkların Yüklenmesi

Proje, yerel C++ derleyicilerine ihtiyaç duymayan en güncel **H3 v4+** API standartlarında ve Python 3.13 uyumlu modern veri bilimi kütüphaneleriyle inşa edilmiştir:

```bash
pip install h3 pandas geopandas duckdb networkx requests shapely

```

### 2. Pipeline'ın Tetiklenmesi

Boru hattını çalıştırarak verileri temizlemek, GeoParquet bölümlemesini yapmak ve `index3.html` interaktif harita arayüzünü ihraç etmek için:

```bash
python main3_final.py

```

### 3. Yerel HTTP Web Servisinin Başlatılması

Tarayıcıların katı yerel dosya güvenlik (`CORS`) ve `Strict-Origin-When-Cross-Origin` politikalarını aşarak haritayı sıfır hata ile açmak için dizinde hafif bir HTTP sunucusu ayağa kaldırılmalıdır:

```bash
python -m http.server 8000

```

Sunucu tetiklendikten sonra tarayıcınızdan **`http://localhost:8000/index3.html`** adresine giderek gerçek sokak şebekesine kıvrımlarla tam oturmuş dinamik yeşil rotayı izleyebilirsiniz.

---

## Gelecek Çalışmalar (Future Work)

* Mimarinin Apache Kafka / Apache Flink ile ultra-düşük gecikmeli gerçek zamanlı akış moduna taşınması.
* Akıllı kavşakların SCADA şebekelerinden canlı sinyalizasyon (ışık faz) verilerinin enjeksiyonu.
* Hücresel H3 matrisleri üzerinde geçmiş zaman serilerinden geleceğe yönelik zamansal akış kestirimi yapabilen **Derin Öğrenme (LSTM - Yapay Sinir Ağları)** modellerinin maliyet fonksiyonuna dahil edilmesi.

---

## Atıf (Citation)

Bu çalışmayı akademik makalelerinizde veya projelerinizde kullanırsanız lütfen aşağıdaki şekilde atıfta bulununuz:

```text
@article{konyah3routing2026,
  title={GeoParquet, DuckDB ve Uber H3 Tabanlı Bulut-Yerli Dinamik Rota Optimizasyon Çerçevesi},
  author={Alper, Alper},
  journal={Selçuk Üniversitesi Bilgisayar Bilimlerinde Coğrafi Bilgi Sistemleri},
  year={2026}
}

```
