# Y-CGC V4 — Operational Completion Plan

Dokumen ini melengkapi `AI-BLUEPRINT.md`. Blueprint adalah arah produk;
dokumen ini adalah daftar implementasi dan cara memastikan sistem bekerja.

## Prinsip yang tidak berubah

- Discovery tidak memakai keyword atau daftar creator sebagai sumber utama.
- Hanya YouTube Shorts yang terverifikasi yang boleh menjadi evidence.
- Video, topik, atau klaim event tidak pernah dibuat hanya agar dashboard ramai.
- Apify memperluas coverage sebagai lane tambahan; ia tidak boleh menentukan
  topik sendiri atau mengalahkan lane anonim.
- Evidence SEO-hijacking disimpan untuk audit, tetapi dikarantina dari ranking.

## Rantai otomatis yang aktif

1. **Discovery** — cohort feed Shorts anonim per region/language; sumber
   tambahan official/latest dan Apify channel panel dicatat terpisah.
2. **Format gate** — setiap evidence harus berstatus `VERIFIED_SHORTS`.
   Video landscape atau video pendek biasa yang lolos pencarian tidak boleh
   masuk ranking hanya karena durasinya pendek.
3. **Source health** — setiap cohort menyimpan seen, unique, repeat, fresh
   0–24h, fresh 24–72h, rejected, dan error ke `market_source_runs`.
4. **Semantic queue** — evidence paling fresh didahulukan. Fingerprint
   menggunakan gateway OpenAI-compatible dengan model `heza-AI/gpt-5.6`.
5. **Topic construction** — hanya pola semantik lintas-channel yang cukup
   koheren masuk kandidat. Tema besar dan event spesifik dipisahkan.
6. **Content truth** — kandidat event dan judul yang berulang dicocokkan
   terhadap transcript/thumbnail. Dua mismatch independen mengarantina
   klaimnya sebagai metadata mismatch.
7. **Ranking** — view lama tidak dibuat sebagai velocity baru. Skor memakai
   pertumbuhan organic evidence lama, evidence fresh baru, diversity channel/
   region/source, freshness, serta history snapshot.
8. **Early signals** — Shorts fresh dan low-view dipantau maksimal 72 jam.
   Topik awal harus memiliki minimal dua channel independen; hasil akhirnya
   dicatat untuk active-learning, bukan mengubah rule secara diam-diam.
9. **Evaluation** — benchmark external, human review, false merge, mismatch,
   lifecycle, dan outcome early signal disimpan sebagai audit.

## Halaman yang harus dipakai

- `/youtube/trends` — topik yang sudah melewati quality gate.
- `/youtube/early-topics` — hipotesis lintas-channel yang masih diuji;
  panel diagnostik menunjukkan seed dan kandidat agar halaman kosong dapat
  dijelaskan tanpa memalsukan topik.
- `/youtube/report` — bukti coverage 24 jam, source health, repeat versus
  unique, freshness, status format, audit content truth, serta backlog AI.
- `/youtube/review` — satu keputusan manusia yang sederhana untuk kandidat
  ambigu; review tidak menghapus raw evidence.
- `/youtube/evaluation` — outcome active-learning dan benchmark pembanding.

## Kriteria sehat yang dipantau

1. `unique_shorts` bertambah pada cohort, bukan hanya `repeat`.
2. Ada Shorts fresh 0–24h yang masuk dari lebih dari satu region/lane.
3. `semantic_backlog` menurun setiap batch; evidence paling fresh diproses
   lebih dahulu.
4. Event publik memiliki content-truth aligned; metadata mismatch tidak
   muncul sebagai topic/event publik.
5. Early topic hanya muncul jika evidence benar-benar independen dan belum
   melewati lifecycle 72 jam.

## Batas yang jujur

YouTube tidak menyediakan API publik untuk seluruh feed Shorts global atau
ranking topic setara TikTok Studio. Sistem ini tidak mengklaim angka global;
ia menghasilkan ranking dari coverage terukur yang dikumpulkan sendiri.
Menambah Apify dapat memperbesar sample dan mempercepat bukti lintas-channel,
tetapi tidak mengubahnya menjadi firehose resmi YouTube.
