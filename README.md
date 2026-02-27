# 🎬 CarGurus Vehicle Video Pipeline

Automated pipeline that scrapes San Antonio Dodge's CarGurus inventory, extracts vehicle photos and window stickers, generates compelling 15-second video scripts, and produces AI-generated cinematic videos using Google Veo and OpenAI Sora.

## Architecture

```
CarGurus Scraper → Photo/Sticker Downloader → Script Generator → Video Producer → Output
     │                      │                        │                  │
     ├─ Playwright          ├─ Vehicle photos        ├─ Gemini API      ├─ Veo 3.1 (primary)
     └─ BeautifulSoup       └─ Window stickers       └─ Claude/GPT      └─ Sora 2 (overflow)
```

## Features

- **Inventory Scraping**: Automated scraping of CarGurus dealer pages using Playwright
- **Asset Download**: Downloads all vehicle photos and window sticker images
- **AI Script Generation**: Creates captivating 15-second video scripts using Gemini
- **Dual Video Generation**: Uses Google Veo (primary, via AI Pro subscription) with Sora 2 fallback
- **Video Stitching**: Combines 8-second clips into 15-second final videos
- **Progress Tracking**: SQLite database tracks every vehicle through the pipeline
- **Cost Tracking**: Monitors API spend per vehicle and total

## Setup

### 1. Clone and Install

```bash
git clone git@github.com:YOUR_USERNAME/cargurus-video-pipeline.git
cd cargurus-video-pipeline
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run the Pipeline

```bash
# Full pipeline
python main.py

# Individual steps
python main.py --step scrape      # Scrape inventory only
python main.py --step download    # Download photos/stickers only
python main.py --step scripts     # Generate video scripts only
python main.py --step videos      # Generate videos only
python main.py --step status      # Show pipeline status
```

## Configuration

Edit `config/settings.py` or set environment variables:

| Variable | Description | Required |
|---|---|---|
| `GOOGLE_API_KEY` | Gemini API key (for scripts + Veo) | Yes |
| `OPENAI_API_KEY` | OpenAI API key (for Sora fallback) | Yes |
| `CARGURUS_DEALER_URL` | Your CarGurus dealer page URL | Yes |
| `MAX_VEHICLES` | Max vehicles to process per run | No (default: all) |
| `VIDEO_QUALITY` | `fast` / `standard` / `pro` | No (default: fast) |
| `COST_LIMIT` | Max $ spend per run | No (default: 50) |

## Cost Estimates (per vehicle)

| Component | Cost |
|---|---|
| Scraping + Downloads | Free |
| Script Generation (Gemini) | ~$0.001 |
| Video - Veo Fast (15s = 2 clips) | ~$2.40 |
| Video - Sora Standard (15s) | ~$1.50 |
| **Total per vehicle (Veo Fast)** | **~$2.40** |
| **Total per vehicle (Sora Std)** | **~$1.50** |

## Project Structure

```
cargurus-video-pipeline/
├── main.py                  # Pipeline orchestrator
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py          # Configuration management
├── scraper/
│   ├── __init__.py
│   ├── cargurus_scraper.py  # CarGurus inventory scraper
│   └── asset_downloader.py  # Photo & sticker downloader
├── scripts/
│   ├── __init__.py
│   └── script_generator.py  # AI video script generator
├── video_gen/
│   ├── __init__.py
│   ├── veo_generator.py     # Google Veo video generation
│   ├── sora_generator.py    # OpenAI Sora video generation
│   └── video_stitcher.py    # Clip combiner
├── utils/
│   ├── __init__.py
│   ├── database.py          # SQLite tracking database
│   └── cost_tracker.py      # API cost monitoring
└── output/
    ├── photos/              # Downloaded vehicle photos
    ├── stickers/            # Window sticker images
    ├── scripts/             # Generated video scripts
    └── videos/              # Final generated videos
```

## License

Private - Silva Consulting Group
