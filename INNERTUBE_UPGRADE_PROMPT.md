# Prompt: YCGC v4 — Upgrade YouTube Client ke General Video Discovery

> **Tujuan:** Ganti `youtube_client.py` supaya bisa mengambil **semua video YouTube** (bukan cuma Shorts), seperti LenosTube.  
> **Konteks:** YCGC v4 sudah pakai InnerTube (undocumented API) untuk Shorts discovery. Tinggal tambah endpoint untuk general video.  
> **Instruksi:** Baca prompt ini, pahami arsitektur yang ada, lalu implementasikan perubahan.

---

## 1. Apa Itu InnerTube?

YouTube punya **2 API**:

| | Public API v3 | InnerTube (Undocumented) |
|---|---|---|
| Dokumentasi | ✅ | ❌ (dipakai YouTube sendiri) |
| Quota | 10.000 unit/hari | **Unlimited** |
| Akses video | Hanya trending/search | **Semua video** |
| Dipakai oleh | Developer biasa | yt-dlp, Invidious, YouTube web client |

**"Undocumented"** artinya Google tidak pernah mengumumkan API ini ke publik. Tapi YouTube sendiri pakai API ini untuk web client-nya (youtube.com). Tools open-source seperti `yt-dlp` (90k+ GitHub stars) sudah me-reverse-engineer API ini sejak 2018.

**Apakah aman?** Untuk metadata (title, views, trending rank) — **ya, aman.** Google hanya agresif memblokir download video, bukan metadata collection. LenosTube, Social Blade, NoxInfluencer juga pakai cara yang sama.

---

## 2. Kondisi YCGC v4 Saat Ini

File `backend/app/services/youtube_client.py` sudah menggunakan InnerTube, tapi **hanya untuk Shorts**. Cara kerjanya:

1. **Bootstrap:** Buka `youtube.com/shorts` → ekstrak `INNERTUBE_API_KEY` + `INNERTUBE_CLIENT_VERSION` dari HTML
2. **Discovery:** Pakai endpoint `youtubei/v1/reel/reel_item_watch` untuk dapat rekomendasi Shorts
3. **Metadata:** Pakai endpoint `youtubei/v1/player` untuk detail video

Kode sudah bagus — ada parsing, rate limiting, deduplikasi. Tinggal tambah endpoint untuk general video.

---

## 3. Apa yang Perlu Ditambah

### 3.1 Endpoint Baru yang Dibutuhkan

InnerTube punya beberapa endpoint yang bisa dipakai untuk general video (non-Shorts):

| Endpoint | Fungsi | Data yang Didapat |
|----------|--------|-------------------|
| `youtubei/v1/browse` | Browse trending/explore | Trending videos per region, homepage feed |
| `youtubei/v1/search` | Search | Hasil pencarian dengan keyword |
| `youtubei/v1/player` | **Sudah ada** | Video detail (views, likes, channel, dll) |

### 3.2 Yang Perlu Diimplementasi

#### A. `browse_trending()` — General Video Trending

Ganti atau tambah method yang mengambil trending video (bukan Shorts):

```python
def browse_trending(self, region: str = "US") -> list[dict]:
    """
    Ambil trending videos dari YouTube trending page.
    Endpoint: POST /youtubei/v1/browse
    """
    response = self.client.post(
        f"{YOUTUBE_ORIGIN}/youtubei/v1/browse",
        params={"key": api_key},
        headers={"X-Youtube-Client-Name": "1", "X-Youtube-Client-Version": client_version},
        json={
            "context": {"client": {"clientName": "WEB", "clientVersion": client_version, "hl": self.language, "gl": region}},
            "browseId": "FEtrending",  # YouTube trending page
        }
    )
    # Parse response -> extract video items
```

**Catatan:** `FEtrending` adalah browse ID untuk halaman trending. Parameter `gl` (region) menentukan trending negara mana.

#### B. `search_videos()` — Search YouTube

```python
def search_videos(self, query: str, max_results: int = 50) -> list[dict]:
    """
    Cari video dengan keyword.
    Endpoint: POST /youtubei/v1/search
    """
    response = self.client.post(
        f"{YOUTUBE_ORIGIN}/youtubei/v1/search",
        params={"key": api_key},
        json={
            "context": {"client": {"clientName": "WEB", "clientVersion": client_version, "hl": self.language, "gl": self.region}},
            "query": query,
            "params": "EgQIBRAB",  # filter: videos only (bukan channel/playlist)
        }
    )
```

#### C. `get_video_details()` — Perluas Player Metadata

Method `_fetch_player_metadata()` sudah ada. Perluas untuk mengekstrak lebih banyak field:

```python
# Field tambahan yang bisa diekstrak dari player response:
# - likeCount (dari videoDetails atau engagement panels)
# - commentCount
# - category
# - tags / keywords
# - duration (seconds)
# - isLive
# - captions available
# - channel subscriber count
# - related videos
```

### 3.3 Konfigurasi Baru di `config.py`

```python
# General Video Trends (bukan Shorts)
youtube_general_trending_enabled: bool = True
youtube_general_trending_regions: str = "ID,US,GB,JP,BR,IN,MX,KR,DE,FR"
youtube_general_trending_interval_minutes: int = 30
youtube_general_trending_max_results: int = 50
youtube_general_search_enabled: bool = True
youtube_general_search_keywords: str = "coffee,vlog,music,gaming,tech"
```

---

## 4. Arsitektur yang Harus Dipertahankan

### 4.1 Jangan Hapus yang Sudah Ada

- `YoutubeAnonymousClient` tetap untuk Shorts discovery
- Shorts pipeline (Early Signals, Breakout) tetap jalan
- General video pipeline adalah **lane terpisah** — tidak mengganggu Shorts

### 4.2 Rate Limiting & Safety

```python
# Di semua method baru, tambahkan:
import time
time.sleep(2.0)  # 1 request per 2 detik maksimum

# Rotate user-agent per session
# Gunakan visitor_data yang berbeda per region
# Simpan api_key + client_version (refresh tiap 1 jam)
```

### 4.3 Struktur File

```
backend/app/services/
├── youtube_client.py          # Tetap untuk Shorts discovery (sudah ada)
├── youtube_general_client.py  # BARU: General video discovery (trending + search)
└── youtube_innertube_base.py  # OPSIONAL: Base class shared antara keduanya
```

### 4.4 Task Baru di Celery

```python
# backend/app/tasks/youtube_general_tasks.py
@celery_app.task
def collect_general_trending():
    """Collect trending videos from multiple regions."""
    for region in settings.youtube_general_trending_regions.split(","):
        videos = client.browse_trending(region.strip())
        for video in videos:
            db.upsert_general_video(video)

@celery_app.task
def collect_general_search():
    """Search YouTube for keyword-based discovery."""
    for keyword in settings.youtube_general_search_keywords.split(","):
        videos = client.search_videos(keyword.strip())
        for video in videos:
            db.upsert_general_video(video)
```

---

## 5. Response Parsing

InnerTube response format:

```json
{
  "contents": {
    "twoColumnBrowseResultsRenderer": {
      "tabs": [{
        "tabRenderer": {
          "content": {
            "sectionListRenderer": {
              "contents": [{
                "itemSectionRenderer": {
                  "contents": [{
                    "videoRenderer": {
                      "videoId": "dQw4w9WgXcQ",
                      "title": {"runs": [{"text": "Video Title"}]},
                      "viewCountText": {"simpleText": "1.2M views"},
                      "lengthText": {"simpleText": "3:45"},
                      "ownerText": {"runs": [{"text": "Channel Name"}]},
                      "publishedTimeText": {"simpleText": "2 days ago"},
                      "thumbnail": {"thumbnails": [{"url": "https://..."}]}
                    }
                  }]
                }
              }]
            }
          }
        }
      }]
    }
  }
}
```

Helper untuk parsing:

```python
def _extract_video_renderers(self, payload: dict) -> list[dict]:
    """Extract all videoRenderer objects from a browse/search response."""
    renderers = []
    for node in self._walk_dicts(payload):
        if "videoRenderer" in node:
            renderers.append(node["videoRenderer"])
    return renderers

def _parse_video_renderer(self, renderer: dict) -> dict:
    """Parse a single videoRenderer into a normalized dict."""
    return {
        "video_id": renderer.get("videoId"),
        "title": self._first_text(renderer, ("title",)),
        "channel_name": self._first_text(renderer, ("ownerText", "longBylineText")),
        "channel_id": renderer.get("ownerText", {}).get("runs", [{}])[0].get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId"),
        "view_count": self._parse_view_count(self._first_text(renderer, ("viewCountText",))),
        "duration": self._first_text(renderer, ("lengthText",)),
        "published_at": self._parse_relative_time(self._first_text(renderer, ("publishedTimeText",))),
        "thumbnail_url": self._first_thumbnail_url(renderer),
        "is_short": self._is_short(renderer),  # true jika dari Shorts shelf
    }
```

---

## 6. Integrasi dengan Pipeline yang Sudah Ada

### 6.1 Model Database

Tambahkan model baru untuk general video (atau gunakan `MarketVideoObservation` yang sudah ada):

```python
# backend/app/models/general_video.py
class GeneralVideoObservation(Base):
    __tablename__ = "general_video_observations"
    id = Column(Integer, primary_key=True)
    video_id = Column(String, index=True)
    title = Column(String)
    channel_name = Column(String)
    channel_id = Column(String)
    view_count = Column(BigInteger)
    like_count = Column(BigInteger)
    duration = Column(String)
    region = Column(String)
    source = Column(String)  # "trending" | "search"
    observed_at = Column(DateTime, default=func.now())
```

### 6.2 API Endpoint

Tambahkan route untuk menampilkan general video trends:

```python
# backend/app/api/endpoints/general_trends.py
@router.get("/youtube/general/trending")
def get_general_trending(region: str = "ID", limit: int = 50):
    """Get general YouTube trending videos (bukan Shorts)."""
    ...
```

### 6.3 Frontend

Halaman baru di Next.js: `frontend/src/app/youtube/general-trends/page.tsx`

---

## 7. Roadmap Implementasi

### Step 1: Tambah method baru di `youtube_client.py`
- `browse_trending(region)` 
- `search_videos(query)`
- `_extract_video_renderers(payload)`
- `_parse_video_renderer(renderer)`

### Step 2: Tambah config
- `youtube_general_trending_regions`
- `youtube_general_trending_interval_minutes`
- `youtube_general_search_keywords`

### Step 3: Buat task Celery
- `collect_general_trending()`
- `collect_general_search()`

### Step 4: Tambah model database
- `GeneralVideoObservation`

### Step 5: Buat API endpoint
- `GET /api/youtube/general/trending`

### Step 6: Buat frontend page
- `frontend/src/app/youtube/general-trends/page.tsx`

---

## 8. Penting: Jangan Rusak yang Sudah Ada

- ❌ Jangan ubah flow Shorts discovery
- ❌ Jangan ubah Early Signals pipeline
- ❌ Jangan ubah Market Trends pipeline
- ✅ Tambah lane baru yang isolated
- ✅ Gunakan config flag `youtube_general_trending_enabled` untuk toggle

---

## 9. Referensi

- InnerTube endpoints: https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube.py
- Invidious (alternative YouTube frontend): https://github.com/iv-org/invidious
- Python innertube library: https://pypi.org/project/innertube/
- YCGC v4 existing code: `backend/app/services/youtube_client.py` (sudah pakai InnerTube)