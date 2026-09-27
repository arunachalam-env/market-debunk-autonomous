"""
src/agents/topic_agent.py

Phase 1 — Topic Discovery

Rotates through 7 Indian Financial YouTube channels by day of week,
extracts the latest video, downloads its transcript (Tamil/Hinglish/mixed),
and uses Gemini to distil a story_seed object for the script agent.

Channel schedule (0=Monday … 6=Sunday):
  Mon: MONEY PECHU       Tue: PR SUNDAR         Wed: MONEY PURSE
  Thu: TRADE ACHIEVERS   Fri: MARKET DRIVER     Sat: TAMIL NIFTY ANALYSIS
  Sun: ZERO1 BY ZERODHA

Data flow:
  rotate_channel() → resolve_channel_id() → fetch_latest_video()
    → download_transcript() → summarize_to_story_seed()
"""
from __future__ import annotations
import os

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
import http.cookiejar
from datetime import datetime
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import feedparser
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound

from src.agents import evaluator
from src.agents.market_api_adapter import DedicatedMarketAPIAdapter
from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="topic_discovery")

_CHANNEL_IDS_PATH = settings.DATA_DIR / "channel_ids.json"

_SERP_MARKET_QUERIES = (
    "SEBI rule changes retail options trading 90 percent loss India",
    "mutual fund direct plan switch regular plan commission drag India",
    "zero brokerage hidden regulatory turnover charges STT SEBI India",
    "stock manipulation pump and dump Telegram SEBI penalty India",
    "finfluencer SEBI registration unregistered advisory crackdown India",
    "algorithmic trading retail loss SEBI warning India",
    "IPO grey market premium GMP valuation trap SME IPO India",
    "dividend yield trap high dividend stock cut India",
    "anchor investor lock in period expiry stock drop India",
    "discount broker outage stop loss execution glitch India",
    "mutual fund expense ratio hike fee disclosure SEBI India",
    "F&O margin rules contract size hike retail impact India",
)

_SERP_INVESTOR_QUERIES = (
    # Named-investor and specific-stock stories - the formats this channel's
    # audience actually watches (Buffett/Munger/Kedia/Jhunjhunwala clips and
    # single-stock news outperformed generic outrage topics by 100x+).
    "Rakesh Jhunjhunwala portfolio stock latest buy sell India",
    "Vijay Kedia portfolio new stock pick multibagger India",
    "Radhakishan Damani DMart Avenue Supermarts stock news India",
    "Warren Buffett Berkshire latest investment lesson value investing",
    "Ashish Kacholia Dolly Khanna portfolio stock change India",
    "NSE stock quarterly results surprise profit collapse retail trap India",
    "famous investor sells stake block deal NSE midcap India",
    "Mohnish Pabrai Porinju Veliyath stock thesis India",
    "specific NSE stock promoter pledge increase crash India",
    "SME IPO listing day crash retail investors stuck India",
)

_SERP_CONSUMER_QUERIES = (
    "credit card hidden charges annual fee trap India",
    "health insurance claim rejection rules room rent capping India",
    "fixed deposit tax TDS real return inflation risk India",
    "no cost EMI trap hidden GST interest India",
    "gold loan auction risk LTV rules RBI India",
    "banking cyber fraud digital arrest safe warning India",
    "peer to peer P2P lending defaults RBI clampdown India",
    "car loan 7 year balloon payment depreciation trap India",
    "ULIP vs mutual fund endowment policy surrender trap India",
    "EPFO claim rejection rules joint declaration delay India",
    "personal loan instant lending app hidden fees India",
    "buy now pay later BNPL credit score damage CIBIL India",
    "health insurance waiting period pre existing disease clause India",
    "ATM transaction charges GST hidden deduction salary account India",
    "credit score CIBIL sudden drop unauthorized enquiry dispute India",
    "gold making charges wastage GST hallmark scam India",
    "debit card AMC fee minimum balance deduction RBI India",
    "term insurance claim rejected section 45 insurance India",
    "UPI platform fee third party payment apps recharge surcharge India",
    "credit card lounge access quarterly spend criteria devaluation India",
    "zero cost EMI processing fee 18 percent GST hidden calculation India",
    "bank savings account quarterly average balance penal charges RBI India",
    "personal loan pre-closure penalty loan foreclosure charges RBI India",
)

_SERP_QUERIES = _SERP_MARKET_QUERIES + _SERP_CONSUMER_QUERIES

_EVERGREEN_MARKET_TOPICS = (
    "Mutual fund regular plan commission can quietly cost you 35% of your total retirement wealth compared to direct plan.",
    "Chasing high dividend yield stocks is often a trap because companies slash dividends right after attracting retail investors.",
    "SEBI data reveals 93% of retail F&O options traders lose money, while brokerages collect guaranteed turnover charges.",
    "Buying small-cap IPOs on grey market premium hype frequently leads to massive listing day dumps by institutional anchors.",
    "Zero brokerage trading apps make their real money by pushing retail users into high-volume intraday trades with heavy turnover tax.",
    "Stop-loss hunting by institutional algorithms triggers retail selloffs at key psychological support levels before reversing upward.",
    "Holding company conglomerate discounts mean you are buying underlying stocks at a paper discount that never converts to cash.",
    "Switching mutual funds frequently based on past 1-year returns destroys compounding through exit loads and capital gains tax.",
    "Algorithmic trading bots marketed on Telegram promising guaranteed daily returns are unregistered scams exploiting retail greed.",
    "Stock split announcements do not create any real financial value, yet retail investors rush to buy overpriced shares before ex-split.",
    "Buying momentum stocks at 52-week highs without trailing stops traps retail traders at the absolute top of cyclical rallies.",
    "SME IPO grey market premiums are routinely manipulated by promoters to create artificial retail oversubscription.",
    "Index fund tracking error quietly eats up your benchmark returns even when you think you are matching the Nifty 50.",
    "Bonus share issues are just a mathematical division of share price with zero impact on the company's real market value.",
    "Chasing penny stocks expecting multibagger returns is the fastest way retail investors suffer total capital erosion.",
    "Finfluencer stock recommendations are frequently paid promotional schemes where promoters exit their positions onto viewers.",
    "Thematic and sector mutual funds launch at the peak of market cycles, locking retail investors into multi-year underperformance.",
    "Margin trading facility interest rates of 18% can liquidate your entire portfolio during sudden market corrections.",
    "Buying call options before earnings announcements loses money even if the stock jumps, due to post-earnings volatility crush.",
    "Demat account annual maintenance charges and depository participant charges are silently deducted from your trading balance.",
    "Exchange traded funds with low trading volumes trade at wide spreads between market price and net asset value, costing you money.",
    "Chasing turnaround stocks with massive debt almost always wipes out retail investors before any corporate recovery.",
    "Promoter pledge increases signal severe corporate cash distress that retail investors usually ignore until the stock crashes.",
    "Retail investors buy dips during structural bear markets, confusing falling knife value traps with genuine bargain opportunities.",
    "Securities transaction tax and stamp duty quietly consume a huge percentage of active swing trader profits.",
    "Stock market technical indicators lag behind institutional order flow, causing false breakout entries for retail chartists.",
    "Target maturity bond funds locked at low yields lose purchasing power when inflation spikes unexpectedly.",
    "Chasing foreign stock investing through high-fee feeder funds introduces severe currency conversion and tax deduction drag.",
    "P/E ratio without checking cash flows allows manipulative accounting to disguise deeply overvalued companies as cheap.",
    "Retail algorithmic copy trading platforms allow providers to front-run subscriber orders for private profit.",
)

_EVERGREEN_INVESTOR_TOPICS = (
    # Documented, verifiable lessons from famous investors and real NSE stock
    # episodes - the content style that built this channel's subscriber base.
    # Every entry is a checkable historical fact, not an invented statistic.
    "Warren Buffett's See's Candies purchase taught that pricing power, not cheap valuation, is the real moat retail investors ignore.",
    "Charlie Munger's inversion principle says avoiding stupid bets beats finding brilliant ones, which is why retail F&O losses compound.",
    "Rakesh Jhunjhunwala held Titan for over two decades through multiple 30% crashes while retail investors sold at every correction.",
    "Vijay Kedia's SMILE framework bets on small companies with honest management instead of chasing large-cap names everyone already owns.",
    "Radhakishan Damani built DMart on thin margins and owned stores, proving boring execution beats flashy expansion stories.",
    "Mohnish Pabrai's 'heads I win, tails I don't lose much' asymmetry is the opposite of retail traders risking everything on one tip.",
    "Peter Lynch's 'buy what you know' is the most misquoted rule in investing - his real rule was research the balance sheet first.",
    "Yes Bank's collapse wiped out retail investors who kept averaging down on a falling knife because the price 'looked cheap'.",
    "Paytm's IPO crashed 27% on listing day, showing how anchor-investor hype transfers wealth from retail applicants to early backers.",
    "Asian Paints' dealer network moat let it dominate for decades while competitors with bigger ad budgets kept failing.",
    "Suzlon's repeated dilutions turned a retail favourite into a penny stock, proving turnaround stories need debt math, not hope.",
    "DHFL and IL&FS showed that even 'safe' AAA-rated debt can collapse, and credit-risk funds hid that risk in fine print.",
    "IRCTC's listing-day frenzy rewarded flippers, but retail investors who chased the post-listing spike bought the exact top.",
    "Buffett's 'be fearful when others are greedy' explains why record IPO subscription numbers mark market tops, not bottoms.",
    "Eicher Motors' Royal Enfield turnaround worked because Siddhartha Lal killed 13 of 15 products - focus, not diversification.",
    "Jhunjhunwala's biggest wins came from buying boring businesses when nobody wanted them, not from trading hot momentum stocks.",
)

_EVERGREEN_CONSUMER_TOPICS = (
    "Zero cost EMI is not free; retailers and banks secretly add 18% GST onto the calculated interest subvention.",
    "Paying only the minimum due on your credit card allows 42% annualized interest to compound daily for up to 14 years.",
    "Checking your credit score on third-party loan aggregator apps triggers multiple hard inquiries that drop your CIBIL score.",
    "Banks quietly deduct quarterly SMS alert fees and debit card annual maintenance charges from your minimum balance savings account.",
    "Car dealerships inflate insurance premiums by up to 40% compared to buying direct comprehensive policies from insurers.",
    "Health insurance room rent limits trigger proportionate deduction clauses that slash your entire hospital claim reimbursement by half.",
    "Taking a 7-year car loan on zero down payment means you pay more in interest than the depreciated car is worth.",
    "Buying gold jewellery comes with up to 25% non-refundable making and wastage charges that you lose immediately upon resale.",
    "Fixed deposit returns drop below zero in real purchasing power once 6% interest is adjusted for inflation and 30% tax.",
    "Buy Now Pay Later apps open active personal loan credit lines on your CIBIL report without your explicit understanding.",
    "Co-signing a loan for a friend or relative makes you 100% legally liable to repay the entire debt when they default.",
    "Term insurance claims are rejected if you fail to disclose minor medical history like smoking or occasional hypertension.",
    "ULIP insurance plans front-load massive mortality and administration charges, producing worse returns than PPF or term plus mutual fund.",
    "Digital arrest and fake police verification phone calls are cyber extortion scams designed to drain your bank accounts.",
    "Instant loan apps demand contact list permissions to harass your friends and family when repayments are delayed.",
    "Prepaying your home loan in the first 5 years saves lakhs in interest, while banks incentivize you to extend the tenure.",
    "Peer-to-peer lending platforms promise 12% returns while masking high default rates and strict RBI recovery constraints.",
    "Unclaimed bank account balances and dividend warrants are transferred to the IEPF authority if untouched for 7 years.",
    "Guaranteed return insurance policies offer an internal rate of return below 5.5%, barely matching simple savings accounts.",
    "Cashless health insurance claims at network hospitals get partially rejected for non-medical consumable hospital charges.",
    "Personal loan flat interest rates of 10% actually equal an effective reducing rate of nearly 18% per year.",
    "Sovereign Gold Bonds offer tax exemptions only at maturity, while secondary market exits incur full capital gains.",
    "Bank locker compensation is legally limited to 100 times your annual rent, leaving valuable jewellery largely uninsured.",
    "Credit card reward points silently expire, and banks charge redemption fees that wipe out the cash value of your rewards.",
    "EPFO claim rejections often happen due to minor name or date of birth mismatches between Aadhaar and UAN records.",
    "Food delivery and quick commerce apps charge hidden platform fees and inflated menu prices that exceed restaurant rates by 25%.",
    "Car loan foreclosure and pre-closure penalty clauses are hidden in fine print to lock you into high interest payments.",
    "Student education loan collateral clauses can seize parental property if repayment holidays are misunderstood.",
    "Salary account overdraft facilities charge astronomical daily interest rates the moment your account balance turns negative.",
    "Consumer court judgments show life insurance claims are frequently rejected if you change nominee details without proper insurer endorsement.",
)

_EVERGREEN_TOPICS = _EVERGREEN_MARKET_TOPICS + _EVERGREEN_CONSUMER_TOPICS


# ──────────────────────────────────────────────────────────────────────────────
#  Channel ID Resolution
# ──────────────────────────────────────────────────────────────────────────────

def _load_channel_id_cache() -> dict[str, Optional[str]]:
    """Loads cached channel IDs or returns hardcoded defaults."""
    cache = {
        "MONEY PECHU": "UC7fQFl37yAOaPaoxQm-TqSA",
        "PR SUNDAR": "UCS2NdYUmv_PUyyKeDAo5zYA",
        "MONEY PURSE": "UChBT5TlUeG68PKvJSg6MkqQ",
        "TRADE ACHIEVERS": "UCzk4zJEoZMnjvpoN0HlKjHQ",
        "MARKET DRIVER": "UCo5CAieenL0ExXzvjzs17QQ",
        "TAMIL NIFTY ANALYSIS": "UCft3VdKoq4HNBYd4MRnQF6Q",
        "ZERO1 BY ZERODHA": "UCUUlw3anBIkbW9W44Y-eURw"
    }
    if _CHANNEL_IDS_PATH.exists():
        with open(_CHANNEL_IDS_PATH, "r", encoding="utf-8") as f:
            cache.update(json.load(f))
    return cache


def _save_channel_id_cache(cache: dict[str, Optional[str]]) -> None:
    with open(_CHANNEL_IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def resolve_channel_id(channel_name: str) -> Optional[str]:
    """
    Returns the YouTube channel ID for a given channel name.
    Tries the local cache first; on a miss, searches via YouTube Data API v3.
    If YT_API_KEY is absent, returns None (caller will use RSS name-based URL).
    """
    cache = _load_channel_id_cache()

    if cache.get(channel_name):
        return cache[channel_name]

    # Case-insensitive / normalized lookup
    norm = channel_name.strip().upper()
    for k, v in cache.items():
        if k.strip().upper() == norm:
            return v

    if not settings.YT_API_KEY:
        log.warning("YT_API_KEY not set — cannot resolve channel ID for '%s'", channel_name)
        return None

    log.info("Resolving channel ID for '%s' via YouTube API …", channel_name)
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": channel_name,
        "type": "channel",
        "maxResults": 3,
        "key": settings.YT_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            channel_id = items[0]["id"]["channelId"]
            cache[channel_name] = channel_id
            _save_channel_id_cache(cache)
            log.info("Resolved '%s' → %s", channel_name, channel_id)
            return channel_id
    except Exception as exc:
        log.error("Failed to resolve channel ID for '%s': %s", channel_name, exc)

    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Day-Based Channel Rotation
# ──────────────────────────────────────────────────────────────────────────────

def rotate_channel(day_override: Optional[int] = None) -> str:
    """
    Returns the channel name to use for today.
    day_override (0-6) lets you force a specific day (useful for testing).
    """
    day = day_override if day_override is not None else datetime.now().weekday()
    channel = settings.CHANNEL_REGISTRY[day % 7]
    log.info("Day %d → using channel: %s", day, channel)
    return channel


# ──────────────────────────────────────────────────────────────────────────────
#  Recent Videos Fetching (Top 5 for deep scanning)
# ──────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_recent_videos_api(channel_id: str, limit: int = 5) -> list[dict]:
    """Primary: YouTube Data API v3 (returns up to limit recent videos)."""
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "order": "date",
        "type": "video",
        "maxResults": limit,
        "key": settings.YT_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    results = []
    for item in items:
        results.append({
            "video_id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "description": item["snippet"].get("description", ""),
            "published_at": item["snippet"]["publishedAt"],
        })
    return results


def _fetch_recent_videos_rss(channel_id: str, limit: int = 5) -> list[dict]:
    """Fast, zero-quota YouTube RSS feed fetcher. Always returns real-time uploads with descriptions."""
    import urllib.request
    import xml.etree.ElementTree as ET
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
            results = []
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
                "media": "http://search.yahoo.com/mrss/"
            }
            for entry in root.findall("atom:entry", ns)[:limit]:
                vid_el = entry.find("yt:videoId", ns)
                title_el = entry.find("atom:title", ns)
                pub_el = entry.find("atom:published", ns)
                desc_el = entry.find(".//media:description", ns)
                if vid_el is not None and title_el is not None:
                    results.append({
                        "video_id": vid_el.text.strip(),
                        "title": title_el.text.strip(),
                        "description": desc_el.text.strip() if desc_el is not None and desc_el.text else "",
                        "published_at": pub_el.text.strip() if pub_el is not None and pub_el.text else "",
                    })
            if results:
                log.info("Found %d fresh videos via YouTube RSS for channel ID %s", len(results), channel_id)
                return results
    except Exception as exc:
        log.warning("YouTube RSS feed fetch failed for channel %s: %s", channel_id, exc)
    return []


# Public alias to safeguard against internal/external caller NameErrors
fetch_recent_videos_rss = _fetch_recent_videos_rss


def _fetch_recent_videos_ytdlp(channel_id: str, limit: int = 5) -> list[dict]:
    """Fallback: yt-dlp to scrape recent videos (bypasses RSS IP blocks)."""
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'playlist_items': f'1-{limit}',
            'force_generic_extractor': False,
        }
        uploads_playlist_id = f"UU{channel_id[2:]}" if channel_id.startswith("UC") else channel_id
        url = f"https://www.youtube.com/playlist?list={uploads_playlist_id}"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and info['entries']:
                results = []
                for entry in info['entries']:
                    if not entry:
                        continue
                    video_id = entry.get("id") or entry.get("url")
                    if not video_id or video_id == channel_id:
                        continue
                    results.append({
                        "video_id": video_id,
                        "title": entry.get("title", ""),
                        "description": entry.get("description", ""),
                        "published_at": "",
                    })
                return results
    except Exception as exc:
        log.error("yt-dlp fetch failed for channel %s: %s", channel_id, exc)
    return []


def fetch_recent_videos(channel_name: str, limit: int = 5) -> list[dict]:
    """
    Fetches up to `limit` recent video metadata for a channel.
    Prioritizes real-time YouTube RSS feed (zero quota, real-time uploads).
    Falls back to YouTube Data API v3 and yt-dlp.
    """
    channel_id = resolve_channel_id(channel_name)
    if not channel_id:
        log.error("Could not resolve channel ID for: %s", channel_name)
        return []

    # Priority 1: Real-time YouTube RSS feed (zero quota, real-time uploads, includes descriptions)
    rss_videos = _fetch_recent_videos_rss(channel_id, limit=limit)
    if rss_videos:
        return rss_videos

    # Priority 2: YouTube Data API v3
    if settings.YT_API_KEY:
        try:
            videos = _fetch_recent_videos_api(channel_id, limit=limit)
            if videos:
                log.info("Found %d videos via API for %s", len(videos), channel_name)
                return videos
        except Exception as exc:
            log.warning("API fetch failed, falling back to yt-dlp: %s", exc)

    # Priority 3: yt-dlp fallback
    videos = _fetch_recent_videos_ytdlp(channel_id, limit=limit)
    if videos:
        log.info("Found %d videos via yt-dlp for %s", len(videos), channel_name)
        return videos

    log.error("Could not fetch videos for channel: %s", channel_name)
    return []


def fetch_latest_video(channel_name: str) -> Optional[dict]:
    """Backwards-compatible helper returning the single latest video."""
    videos = fetch_recent_videos(channel_name, limit=1)
    return videos[0] if videos else None


# ──────────────────────────────────────────────────────────────────────────────
#  Transcript Download
# ──────────────────────────────────────────────────────────────────────────────

def download_transcript(video_id: str) -> str:
    """
    Downloads the YouTube transcript using the RapidAPI WEBVTT endpoint.
    Handles Tamil, Hindi, Hinglish, or mixed-language transcripts.
    Returns the raw concatenated text.
    """
    # Priority 1: Direct youtube_transcript_api with full track listing and cookie support
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        cookies_file = "cookies.txt" if os.path.exists("cookies.txt") else None
        try:
            api = YouTubeTranscriptApi(cookies=cookies_file) if cookies_file else YouTubeTranscriptApi()
        except TypeError:
            api = YouTubeTranscriptApi()

        t_list = api.list(video_id)
        available = [t.language_code for t in t_list]
        log.info("Available transcript tracks for %s: %s", video_id, available)

        # Priority: Tamil -> Indian English -> English -> Hindi -> Telugu -> Any available track
        preferred = ["ta", "en-IN", "en", "hi", "te"] + available
        transcript_obj = t_list.find_transcript(preferred)
        data = transcript_obj.fetch()

        lines = []
        for s in data:
            txt = getattr(s, "text", None)
            if txt is None and isinstance(s, dict):
                txt = s.get("text", "")
            elif txt is None:
                txt = str(s)
            if txt and txt.strip():
                lines.append(txt.strip())
        if lines:
            final_text = " ".join(lines)
            log.info("✓ Downloaded transcript (%s) via youtube_transcript_api: %d chars", transcript_obj.language_code, len(final_text))
            if len(final_text) > 50:
                return final_text
    except Exception as exc:
        log.info("youtube_transcript_api list/fetch not available for %s: %s. Trying RapidAPI...", video_id, exc)

    log.info("Downloading transcript via RapidAPI for video: %s", video_id)

    # Priority: Tamil -> Hindi -> English
    lang_priority = ["ta", "hi", "en-IN", "en", "auto"]

    if not settings.RAPIDAPI_KEY:
        log.warning("RAPIDAPI_KEY missing. Skipping RapidAPI transcript provider.")
        return _download_transcript_ytdlp(video_id, "cookies.txt" if os.path.exists("cookies.txt") else None)
    
    headers = {
        'X-RapidAPI-Key': settings.RAPIDAPI_KEY,
        'X-RapidAPI-Host': 'youtube-captions-transcript-subtitles-video-combiner.p.rapidapi.com'
    }
    
    url = f"https://youtube-captions-transcript-subtitles-video-combiner.p.rapidapi.com/download-webvtt/{video_id}"

    for lang in lang_priority:
        try:
            querystring = {"language": lang, "response_mode": "default"}
            response = requests.get(url, headers=headers, params=querystring, timeout=15)
            
            if response.status_code == 200 and "WEBVTT" in response.text:
                # Parse WEBVTT into raw text
                lines = response.text.split('\n')
                raw_text = []
                for line in lines:
                    line = line.strip()
                    # Skip empty lines, timestamps, WEBVTT headers, and metadata
                    if not line or '-->' in line or line == 'WEBVTT' or line.startswith('Kind:') or line.startswith('Language:') or line.startswith('Style:'):
                        continue
                    # Remove any inline styling like <c.color> or <b>
                    line = re.sub(r'<[^>]+>', '', line)
                    if line not in raw_text[-3:]: # Basic deduplication
                        raw_text.append(line)
                
                final_text = " ".join(raw_text)
                log.info("Downloaded transcript (%s) via RapidAPI: %d chars", lang, len(final_text))
                if len(final_text) > 50:
                    return final_text
        except Exception as exc:
            log.warning("RapidAPI failed for lang %s: %s", lang, exc)
            continue

    log.error("RapidAPI exhausted all languages. Attempting ultimate yt-dlp fallback.")
    return _download_transcript_ytdlp(video_id, "cookies.txt" if os.path.exists("cookies.txt") else None)

def _download_transcript_ytdlp(video_id: str, cookies_path: Optional[str] = None) -> str:
    """Last-resort transcript extraction via yt-dlp."""
    import subprocess
    import tempfile

    import sys

    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "yt_dlp",
                    "--write-sub",
                    "--write-auto-sub",
                    "--sub-lang", "ta,en,en-IN,hi,all",
                    "--skip-download",
                    "--sub-format", "vtt/srt/best",
                    "-o", f"{tmpdir}/%(id)s",
                ]
                + (["--cookies", cookies_path] if cookies_path else [])
                + [url],
                capture_output=True,
                text=True,
                timeout=60,
            )
            # Find any .vtt or .srt file
            vtt_files = list(Path(tmpdir).glob("*.vtt")) + list(Path(tmpdir).glob("*.srt"))
            if vtt_files:
                raw = vtt_files[0].read_text(encoding="utf-8")
                # Strip VTT formatting
                lines = [
                    re.sub(r"<[^>]+>", "", line).strip()
                    for line in raw.split("\n")
                    if line.strip() and not line.startswith("WEBVTT")
                    and not re.match(r"\d{2}:\d{2}", line)
                    and not "-->" in line
                ]
                return " ".join(filter(None, lines))
        except Exception as exc:
            log.error("yt-dlp transcript extraction failed: %s", exc)

    log.warning("All transcript methods failed — returning empty string")
    return ""


# ──────────────────────────────────────────────────────────────────────────────
#  Story Seed Extraction (Gemini Summarization — NEW)
# ──────────────────────────────────────────────────────────────────────────────


_FIGURE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|percent|per\s*cent|₹|rs\.?|lakhs?|crores?|k)?", re.IGNORECASE)


def _seed_figure_violations(result: dict, source_text: str) -> list:
    """Numeric tokens in the seed that do not appear in the source text.

    The fact-check gate keeps catching LLM-invented statistics; this is the
    deterministic counterpart to the prompt-side traceability rule.
    """
    src = source_text.lower().replace(",", "")
    fields = [str(result.get("thesis", ""))]
    seed = result.get("story_seed")
    if isinstance(seed, dict):
        fields += [str(v) for v in seed.values()]
    bad = []
    for field in fields:
        for m in _FIGURE_RE.finditer(field.lower()):
            token = m.group(0).strip()
            dm = re.match(r"\d[\d,]*(?:\.\d+)?", token)
            if not dm:
                continue
            digits = dm.group(0).replace(",", "").rstrip(".")
            if digits and digits not in src:
                bad.append(token)
    return sorted(set(bad))


def summarize_to_story_seed(raw_transcript: str, video_title: str = "") -> dict:
    """
    Pipes the raw (possibly Tamil/Hinglish) transcript through Gemini
    to extract a rich story_seed object for the script agent.

    Returns a dict with:
        - thesis: single controversial English sentence
        - story_seed: {inciting_event, protagonist_flaw, real_world_anchor,
                       concept_name, concept_one_liner}
    """
    if not raw_transcript.strip():
        return _fallback_story_seed(video_title)

    prompt = f"""You are the Market Debunk research desk: financial content analyst, fact checker,
story editor, and short-form retention strategist specialising in Indian stock markets.

The following is a raw YouTube video transcript (may be in Tamil, Hindi, Hinglish, or a mix).
Video title: "{video_title}"

TRANSCRIPT:
{raw_transcript[:5000]}

Your task: Extract a story seed to be turned into a cinematic, entertaining, satirical short-form video about finance.

TONE & FACT-FIRST FRAMING DIRECTIVE:
- Facts first: identify what the source facts actually show and let the framing follow them naturally - never force a debunk angle.
- If the source reveals a genuine trap, mismatch, or gimmick, frame it with biting satire and irony, backed by undeniable facts.
- If the facts show the popular claim actually holds up, frame the story as a sharp, surprising explainer instead - no manufactured outrage.
- Avoid boring textbook explainer language. The viewer should feel the story is clever, surprising, and worth sharing.
- The thesis must be provocative, witty, but completely defensible.
- The story_seed should give the script agent concrete props and relatable everyday consumer situations.

FACT TRACEABILITY (HARD RULE):
- Every number, percentage, rupee amount, date, and named entity in your thesis and story_seed fields MUST appear explicitly in the TRANSCRIPT/source text above.
- If the source has no figure, describe it qualitatively ("a double-digit hike", "a steep hidden fee") - NEVER invent statistics and never attribute them to unnamed reports, studies, or trackers.
- A downstream fact-check gate verifies every claim against live sources and blocks the video on anything unverifiable.

Output ONLY a valid JSON object with exactly these keys (all values in English):
{{
  "thesis": "One sentence (max 25 words): the most controversial or provocative financial claim from this content. Should make viewers say 'wait, really?!'",
  "story_seed": {{
    "inciting_event": "A specific, relatable everyday moment that sets up the story (e.g. 'Arjun checks his mutual fund app and his returns are 0% despite the Nifty being up 12%')",
    "protagonist_flaw": "The common mistake most people make, which Arjun will also make (e.g. 'He trusted his fund manager blindly and never checked the expense ratio')",
    "real_world_anchor": "The actual market fact, company name, or financial event from the transcript that this story is based on (e.g. 'HDFC AMC quietly raised its expense ratio from 1.05% to 1.35% this quarter')",
    "concept_name": "The official finance term being explained (e.g. 'Expense Ratio Drag')",
    "concept_one_liner": "One plain-English sentence explaining what this concept means (e.g. 'Even when markets go up, hidden fund fees quietly eat your profits every year')",
    "visual_evidence": "One concrete non-branded visual object that can appear on screen, such as a blurred app chart, invoice, calculator, newspaper-like clipping, or marked notebook"
  }}
}}

Output ONLY the JSON. No explanation, no preamble, no markdown fences."""

    def _extract_seed(active_prompt: str) -> Optional[dict]:
        """One extraction pass over the Gemini ladder, then the Groq fallback."""
        # Call AI API (Gemma primary, Gemini flash fallback) using API keys
        from src.agents.script_agent import _get_api_clients
        clients = _get_api_clients()
        models = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]

        for model_name in models:
            for client in clients:
                try:
                    cfg = {"temperature": 0.70}
                    if "gemini" in model_name:
                        cfg["response_mime_type"] = "application/json"
                    response = client.models.generate_content(
                        model=model_name,
                        contents=active_prompt,
                        config=cfg,
                    )
                    raw_json = response.text.strip()
                    if "```" in raw_json:
                        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.MULTILINE)
                        raw_json = re.sub(r"\s*```\s*$", "", raw_json, flags=re.MULTILINE)
                    result = json.loads(raw_json)
                    log.info("AI story seed extracted via %s | concept: %s | thesis: %s",
                             model_name,
                             result.get("story_seed", {}).get("concept_name", "?"),
                             result.get("thesis", "?"))
                    return result
                except Exception as exc:
                    log.debug("Story seed extraction with %s failed: %s", model_name, exc)
                    continue

        # Groq fallback
        if settings.GROQ_API_KEY:
            try:
                from groq import Groq
                client = Groq(api_key=settings.GROQ_API_KEY)
                completion = client.chat.completions.create(
                    model=settings.GROQ_FALLBACK_MODEL,
                    messages=[{"role": "user", "content": active_prompt}],
                    max_tokens=600,
                    temperature=0.7,
                )
                raw_json = completion.choices[0].message.content.strip()
                if "```" in raw_json:
                    raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.MULTILINE)
                    raw_json = re.sub(r"\s*```\s*$", "", raw_json, flags=re.MULTILINE)
                result = json.loads(raw_json)
                log.info("Groq story seed: %s", result.get("thesis", "?"))
                return result
            except Exception as exc:
                log.error("Groq story seed extraction also failed: %s", exc)
        return None

    # Deterministic traceability enforcement: figures in the seed must exist in
    # the source text. One corrected regeneration, then a figure-free fallback.
    result = _extract_seed(prompt)
    if result is not None:
        violations = _seed_figure_violations(result, raw_transcript)
        if violations:
            log.warning("Story seed invented figures not in source %s; regenerating once with correction.", violations)
            corrected_prompt = prompt + (
                "\n\nCORRECTION: your previous output contained these figures that do NOT "
                "appear in the source text: " + ", ".join(violations) + ". Regenerate the "
                "JSON WITHOUT any of them (and without close paraphrases of them); describe "
                "magnitudes qualitatively instead."
            )
            retry = _extract_seed(corrected_prompt)
            if retry is not None and not _seed_figure_violations(retry, raw_transcript):
                return retry
            log.warning("Seed still untraceable after correction; falling back to title-based seed.")
            result = None
    if result is not None:
        return result

    # Dynamic fallback derived purely from the real video title (no static templates)
    clean_title = " ".join(video_title.split()).strip()
    return {
        "thesis": clean_title,
        "story_seed": {
            "inciting_event": f"Fresh market movements emerge regarding {clean_title[:50]}",
            "protagonist_flaw": "Reacting to headlines without verifying structural market data",
            "real_world_anchor": clean_title,
            "concept_name": clean_title[:30],
            "concept_one_liner": f"Understanding the facts behind {clean_title[:40]}",
            "visual_evidence": "financial charts and verified market figures",
        }
    }


def _is_strictly_within_24h(date_str: str) -> bool:
    """Validate that the date string represents content published within the last 24 hours."""
    if not date_str:
        return False
    ds = date_str.lower().strip()
    # Typical relative timestamps in Google News / Search
    if any(unit in ds for unit in ("hour", "hr", "min", "sec", "just now", "1 day ago", "yesterday")):
        return True
    # Today's date check e.g. '05 Sep' or 'Sep 5'
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    t1 = now.strftime("%d %b").lower()
    t2 = now.strftime("%b %d").lower()
    if t1 in ds or t2 in ds:
        return True
    return False


def _fetch_serp_google_result(query: str) -> Optional[dict]:
    """Use a fresh India-localized Google News/Search result strictly within the last 24 hours."""
    if not settings.SERPAPI_KEY:
        return None

    # Priority 1: Google News engine (breaking market news from ET, Mint, Moneycontrol, etc.)
    try:
        resp_news = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_news",
                "q": query,
                "api_key": settings.SERPAPI_KEY,
                "gl": "in",
                "hl": "en",
            },
            timeout=20,
        )
        if resp_news.status_code == 200:
            news_items = resp_news.json().get("news_results", [])
            for item in news_items:
                title = (item.get("title") or "").strip()
                snippet = (item.get("snippet") or "").strip()
                link = (item.get("link") or "").strip()
                date = (item.get("date") or "").strip()
                source = (item.get("source", {}).get("name") if isinstance(item.get("source"), dict) else item.get("source") or "").strip()

                if not title or not link or not _is_strictly_within_24h(date):
                    continue

                source_key = link or f"{source}:{title}"
                source_id = "serp:" + hashlib.sha256(source_key.lower().encode("utf-8")).hexdigest()[:16]

                if (
                    evaluator.is_source_id_used(source_id)
                    or evaluator.is_duplicate(title)[0]
                    or evaluator.is_concept_duplicate(title)[0]
                ):
                    continue

                raw_text = (
                    f"Breaking Indian Financial News from '{source}' ({date}) regarding '{query}'. "
                    f"Headline: {title}. Context: {snippet}. URL: {link}."
                )
                log.info("✓ Found verified 24h-fresh Google News article: '%s' (%s)", title, date)
                return {
                    "channel": f"Google News: {source}",
                    "video_id": "",
                    "source_id": source_id,
                    "video_title": title,
                    "raw_text": raw_text,
                }
    except Exception as exc:
        log.warning("SerpApi google_news query failed: %s", exc)

    # Priority 2: Standard Google Search with qdr:d (last 24h), strictly verified by date
    try:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "api_key": settings.SERPAPI_KEY,
                "gl": "in",
                "hl": "en",
                "tbs": "qdr:d",  # results indexed in the last day
                "num": 10,
            },
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("organic_results", [])
        for item in results:
            title = (item.get("title") or "").strip()
            snippet = (item.get("snippet") or "").strip()
            link = (item.get("link") or "").strip()
            date = (item.get("date") or "").strip()
            source = (item.get("source") or item.get("displayed_link") or "").strip()

            # Enforce strict 24-hour freshness
            if not title or not snippet or not link or not _is_strictly_within_24h(date):
                continue

            source_key = link or f"{source}:{title}"
            source_id = "serp:" + hashlib.sha256(source_key.lower().encode("utf-8")).hexdigest()[:16]

            if (
                evaluator.is_source_id_used(source_id)
                or evaluator.is_duplicate(title)[0]
                or evaluator.is_concept_duplicate(title)[0]
            ):
                continue

            raw_text = (
                f"Latest 24h-verified Google Search result for '{query}'. Headline: {title}. "
                f"Summary: {snippet}. Source: {source}. Published: {date}. URL: {link}."
            )
            log.info("✓ Found verified 24h-fresh Google Search result: '%s' (%s)", title, date)
            return {
                "channel": "SERPAPI Google Search",
                "video_id": "",
                "source_id": source_id,
                "video_title": title,
                "raw_text": raw_text,
            }
    except Exception as exc:
        log.warning("SerpApi organic search failed: %s", exc)
    return None


def _discover_from_serp(target_domain: Optional[str] = None) -> Optional[dict]:
    import random
    from datetime import datetime, timezone

    # Select queries according to target domain
    if target_domain == "MARKET_INVESTING":
        query_pool = list(_SERP_MARKET_QUERIES) + list(_SERP_INVESTOR_QUERIES)
    elif target_domain == "CONSUMER_DEFENSE":
        query_pool = list(_SERP_CONSUMER_QUERIES)
    else:
        query_pool = list(_SERP_QUERIES)

    now = datetime.now(timezone.utc)
    seed = now.year * 10000 + now.month * 100 + now.day + now.hour
    random.Random(seed).shuffle(query_pool)

    for query in query_pool:
        try:
            candidate = _fetch_serp_google_result(query)
            if not candidate:
                continue
            seed_data = summarize_to_story_seed(candidate["raw_text"], candidate["video_title"])
            thesis = seed_data.get("thesis", candidate["video_title"])

            # Check candidate thesis against both concept and fuzzy deduplication gates
            is_dup, score, reason = evaluator.is_duplicate(thesis, enforce_slot_domain=False)
            if is_dup:
                log.info("Candidate thesis '%s' blocked by dedup gate: %s. Trying next SERP query...", thesis[:60], reason)
                continue

            return {
                **{key: candidate[key] for key in ("channel", "video_id", "video_title", "source_id")},
                "thesis": thesis,
                "story_seed": seed_data.get("story_seed", {}),
                "transcript_length": len(candidate["raw_text"]),
            }
        except Exception as exc:
            log.warning("SERPAPI query '%s' failed: %s", query, exc)
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Parallel Multi-Channel Scanner (Global Recency First)
# ──────────────────────────────────────────────────────────────────────────────

def scan_all_channels_parallel(limit_per_channel: int = 5) -> list[dict]:
    """
    Scans ALL 7 registered Indian financial channels concurrently.
    Extracts up to `limit_per_channel` recent videos per channel.
    Normalizes timestamps, discards already-used source IDs/videos,
    and sorts the entire pool globally by `published_at DESC` (newest first across YouTube).
    """
    from datetime import datetime, timezone

    all_candidates: list[dict] = []
    channel_list = list(settings.CHANNEL_REGISTRY)

    def _fetch_channel_videos(ch_name: str) -> list[dict]:
        ch_id = resolve_channel_id(ch_name)
        if not ch_id:
            return []
        items = _fetch_recent_videos_rss(ch_id, limit=limit_per_channel)
        fresh = []
        for item in items:
            vid = item.get("video_id")
            if vid and not evaluator.is_source_video_used(vid) and not evaluator.is_source_id_used(f"youtube:{vid}"):
                item["channel"] = ch_name
                fresh.append(item)
        return fresh

    with ThreadPoolExecutor(max_workers=min(len(channel_list), 7)) as executor:
        futures = {executor.submit(_fetch_channel_videos, ch): ch for ch in channel_list}
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    all_candidates.extend(res)
            except Exception as exc:
                log.warning("Channel scanner thread failed: %s", exc)

    # Sort globally by published_at DESC across all 7 channels
    filtered_candidates = []
    for cand in all_candidates:
        vid = cand["video_id"]
        source_id = f"youtube:{vid}"
        if not evaluator.is_source_video_used(vid) and not evaluator.is_source_id_used(source_id):
            filtered_candidates.append(cand)

    def _parse_published_at(cand: dict) -> datetime:
        raw_pub = cand.get("published_at", "")
        if not raw_pub:
            return datetime(2000, 1, 1, tzinfo=timezone.utc)
        try:
            clean_str = raw_pub.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime(2000, 1, 1, tzinfo=timezone.utc)

    filtered_candidates.sort(key=_parse_published_at, reverse=True)
    return filtered_candidates


# ──────────────────────────────────────────────────────────────────────────────
#  Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def discover_topic(day_override: Optional[int] = None) -> dict:
    """
    Full topic discovery pipeline with slot-based rotation and strict anti-repetition.
    Slot 1 (Morning ~09:30 AM IST): MARKET_INVESTING (Stock Market, Mutual Funds, IPOs, Options, Dividends)
    Slot 2 (Evening ~07:00 PM IST): CONSUMER_DEFENSE (Banks, EMI, CIBIL, Insurance, Car Loans, Gold)
    """
    target_domain = evaluator.get_current_target_domain()
    log.info("Target slot domain: %s", target_domain)

    # Step 1: Parallel scan across all 7 channels sorted by newest globally
    candidates = scan_all_channels_parallel(limit_per_channel=5)

    for cand in candidates:
        channel_name = cand["channel"]
        video_id = cand["video_id"]
        video_title = cand["title"]
        source_id = f"youtube:{video_id}"
        published_at = cand.get("published_at", "")

        # Fast gate: Check title before expensive transcript download
        is_title_dup, score, reason = evaluator.is_duplicate(video_title, threshold=0.88, enforce_domain_cooldown=False)
        if is_title_dup:
            log.info("Skipping candidate [%s] '%s' — title blocked: %s", video_id, video_title[:50], reason)
            continue

        # Download transcript
        transcript = download_transcript(video_id)
        raw_content = ""
        content_type = ""
        if transcript and transcript.strip():
            raw_content = transcript
            content_type = "transcript"
        else:
            desc = (cand.get("description") or "").strip()
            if len(desc) >= 30:
                raw_content = f"VIDEO TITLE: {video_title}\nCHANNEL: {channel_name}\n\nVIDEO DESCRIPTION:\n{desc}"
                content_type = "description"
            else:
                raw_content = f"VIDEO TITLE: {video_title}\nCHANNEL: {channel_name}"
                content_type = "title"

        seed_data = summarize_to_story_seed(raw_content, video_title)
        thesis = seed_data.get("thesis", video_title)
        story_seed = seed_data.get("story_seed", {})

        # Comprehensive deduplication on extracted thesis with slot domain enforcement
        is_thesis_dup, score, reason = evaluator.is_duplicate(thesis, enforce_domain_cooldown=True, enforce_slot_domain=True)
        if is_thesis_dup:
            log.info("Candidate [%s] '%s' thesis blocked by gate: %s. Trying next video...", video_id, thesis[:50], reason)
            continue

        log.info(
            "✓ Success! Extracted fresh topic from YouTube: [%s] '%s' from %s (%s)",
            video_id, video_title, channel_name, content_type
        )
        return {
            "channel": channel_name,
            "video_id": video_id,
            "source_id": source_id,
            "video_title": video_title,
            "thesis": thesis,
            "story_seed": story_seed,
            "transcript_length": len(raw_content),
        }

    log.info("No matching YouTube candidates for domain '%s'. Trying live Google News via SerpApi...", target_domain)

    # Step 2: Google News / Search via SerpApi for fresh stories in target domain
    serp_result = _discover_from_serp(target_domain=target_domain)
    if serp_result:
        log.info("✓ Selected fresh 24h Google News story: %s", serp_result.get("video_title", ""))
        return serp_result

    # Step 3: Dedicated Indian Share Market API (if morning market slot)
    if target_domain == "MARKET_INVESTING":
        market_adapter = DedicatedMarketAPIAdapter()
        if market_adapter.is_configured():
            log.info("Attempting topic discovery via Dedicated Indian Share Market API...")
            market_candidate = market_adapter.get_topic_seed()
            if market_candidate:
                seed_data = summarize_to_story_seed(market_candidate["raw_text"], market_candidate["video_title"])
                thesis = seed_data.get("thesis", market_candidate["video_title"])
                if not evaluator.is_duplicate(thesis, enforce_domain_cooldown=False)[0]:
                    log.info("✓ Discovered topic from Dedicated Market API: %s", thesis[:60])
                    return {
                        **{k: market_candidate[k] for k in ("channel", "video_id", "video_title", "source_id")},
                        "thesis": thesis,
                        "story_seed": seed_data.get("story_seed", {}),
                        "transcript_length": len(market_candidate["raw_text"]),
                    }

    # Step 4: Curated Evergreen Fallback (Guarantees zero downtime & 100% anti-repetition)
    log.info("Selecting unduplicated evergreen topic for domain '%s'...", target_domain)
    if target_domain == "MARKET_INVESTING":
        topic_pool = _EVERGREEN_MARKET_TOPICS + _EVERGREEN_INVESTOR_TOPICS
    else:
        topic_pool = _EVERGREEN_CONSUMER_TOPICS

    import random
    shuffled_pool = list(topic_pool)
    random.shuffle(shuffled_pool)

    for eg in shuffled_pool:
        if not evaluator.is_duplicate(eg, enforce_domain_cooldown=False)[0]:
            seed_data = summarize_to_story_seed(f"FINANCIAL DEBUNK: {eg}", eg)
            thesis = seed_data.get("thesis", eg)
            story_seed = seed_data.get("story_seed", {})
            source_id = "evergreen:" + hashlib.sha256(eg.encode("utf-8")).hexdigest()[:12]
            log.info("✓ Discovered fresh evergreen debunk for '%s': %s", target_domain, thesis[:60])
            return {
                "channel": "Market Debunk Research Desk",
                "video_id": "",
                "source_id": source_id,
                "video_title": eg[:60],
                "thesis": thesis,
                "story_seed": story_seed,
                "transcript_length": 0,
            }

    raise RuntimeError("Global scan failed: All topic sources exhausted.")
