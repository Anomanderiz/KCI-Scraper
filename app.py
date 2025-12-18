import streamlit as st
import os
import time
import requests
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
from io import BytesIO
import shutil
import gc  # Garbage collection

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

# --- Configuration & Setup ---
st.set_page_config(
    page_title="KCI Major Gift Scraper", 
    page_icon="🎁", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- AESTHETICS: Custom CSS Injection ---
def local_css():
    st.markdown("""
        <style>
        /* GLOBAL FONTS */
        .stApp {
            font-family: 'Helvetica Neue', sans-serif;
        }

        /* SIDEBAR styling overrides */
        [data-testid="stSidebar"] {
            background-color: #CC0633 !important; /* Primary Red */
            border-right: 2px solid #000000;
        }
        
        /* MAIN AREA styling overrides */
        .stApp > header {
            background-color: transparent;
        }
        
        /* HEADERS */
        h1, h2, h3, h4, h5, h6 {
            color: #FFFFFF !important;
            text-shadow: 2px 2px 0px #000000; /* Black shadow for clarity */
            font-weight: 800;
        }
        
        /* TABLES / DATAFRAMES */
        [data-testid="stDataFrame"] {
            background-color: #CC0633 !important; /* Primary Red */
            border: 2px solid #000000;
            border-radius: 5px;
            padding: 5px;
        }
        [data-testid="stDataFrame"] div[role="grid"] {
            background-color: #CC0633;
            color: white;
        }
        
        /* BUTTONS */
        .stButton>button {
            background-color: #000000; /* Black for clarity */
            color: #CC0633; /* Primary Red text */
            border: 2px solid #FFFFFF;
            border-radius: 8px;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        .stButton>button:hover {
            background-color: #FFFFFF;
            color: #CC0633;
            border-color: #000000;
            box-shadow: 0 0 10px #000000;
        }

        /* INPUT FIELDS (Text inputs, Number inputs) */
        /* Make them Black so they are readable against the Red */
        .stTextInput>div>div>input, .stNumberInput>div>div>input {
            background-color: #000000;
            color: #FFFFFF;
            border: 1px solid #FFFFFF;
        }
        
        /* ALERTS (Success, Info, Warning) */
        .stAlert {
            background-color: #000000; /* Black background */
            color: #FFFFFF;
            border: 1px solid #FFFFFF;
            border-left: 10px solid #CC0633; /* Primary Red accent */
        }
        
        /* PROGRESS BAR */
        .stProgress > div > div > div > div {
            background-color: #000000; /* Black progress fill */
        }
        
        /* DOWNLOAD BUTTON SPECIFIC */
        [data-testid="stDownloadButton"]>button {
            background-color: #FFFFFF !important;
            color: #A6192E !important;
            border: 2px solid #000000 !important;
        }
        </style>
    """, unsafe_allow_html=True)

local_css()

def get_driver():
    """
    Initializes a headless Chrome browser with AGGRESSIVE memory saving options.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor") 
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--dns-prefetch-disable")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    # LOCATE CHROMIUM BINARY
    chromium_path = shutil.which("chromium") or shutil.which("chromium-browser") or "/usr/bin/chromium"
    if os.path.exists(chromium_path):
        chrome_options.binary_location = chromium_path
    
    # LOCATE CHROMEDRIVER
    driver_path = shutil.which("chromedriver") or "/usr/bin/chromedriver"
    
    if os.path.exists(driver_path):
        service = Service(driver_path)
        return webdriver.Chrome(service=service, options=chrome_options)
    else:
        return webdriver.Chrome(options=chrome_options)

def get_all_article_urls(driver, max_clicks, status_container):
    """
    Navigates the KCI listing page and clicks 'View More'.
    """
    DOMAIN = "https://kciphilanthropy.com"
    LISTING_PATH = "/insights/"
    LISTING_PARAMS = "fwp_categories=major-gift-news"
    full_url = f"{DOMAIN}{LISTING_PATH}?{LISTING_PARAMS}"
    
    status_container.info(f"Navigating to {full_url}...")
    try:
        driver.get(full_url)
    except WebDriverException as e:
        st.error(f"Browser crash on initial load: {e}")
        return []
    
    all_urls = set()
    click_count = 0
    
    status_container.text("Analyzing initial page load...")
    
    HARD_LIMIT_CLICKS = 50 
    
    while click_count < max_clicks and click_count < HARD_LIMIT_CLICKS:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        current_urls = {urljoin(DOMAIN, a['href']) for a in soup.select("a[href*='/major-gift-news-']")}
        
        new_urls = current_urls - all_urls
        all_urls.update(new_urls)
        
        status_container.text(f"Collected {len(all_urls)} unique articles (Page {click_count + 1})...")

        try:
            view_more_button = driver.find_element(By.CLASS_NAME, "fwp-load-more")
            
            if not view_more_button.is_displayed():
                break

            driver.execute_script("arguments[0].scrollIntoView(true);", view_more_button)
            time.sleep(1.0) 
            view_more_button.click()
            click_count += 1
            
            wait = WebDriverWait(driver, 10)
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".facetwp-loading")))
            
        except (NoSuchElementException, TimeoutException):
            break
        except Exception as e:
            status_container.warning(f"Pagination stopped early: {e}")
            break
            
    return list(all_urls)

def parse_single_article(url, session):
    """
    Extracts donor info from a single article URL.
    """
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        
        records = []
        main_content = soup.find("article") or soup.find("main") or soup

        for h2 in main_content.find_all('h2'):
            donor = h2.get_text(strip=True)
            if not donor or "submissions notice" in donor.lower():
                continue

            content_block = []
            for sibling in h2.find_next_siblings():
                if sibling.name == 'h2':
                    break
                content_block.append(sibling)
            
            if not content_block: continue

            info = {"Donor": donor, "Gift": "", "Recipient": "", "City": "", "Province": "", "Date": "", "Description": "", "Source URL": url}
            block_soup = BeautifulSoup("".join(str(s) for s in content_block), "html.parser")
            block_text = block_soup.get_text(separator="\n", strip=True)

            for h3 in block_soup.find_all('h3'):
                label = h3.get_text(strip=True).replace(":", "")
                if label in info:
                    next_elem = h3.find_next_sibling()
                    if next_elem:
                        info[label] = next_elem.get_text(strip=True)

            date_tag = block_soup.find('h6')
            if date_tag:
                info["Date"] = date_tag.get_text(strip=True)
            
            gift_pattern = re.compile(r'\$\d[\d,.]*\s*(million|billion|thousand)?', re.IGNORECASE)
            gift_match = gift_pattern.search(block_text)
            if gift_match:
                info["Gift"] = gift_match.group(0)

            description_text = block_text
            for key, value in info.items():
                if value and key != "Source URL":
                    description_text = description_text.replace(value, "")
            
            for label in ["Recipient", "City", "Province", "Date", "Gift"]:
                 description_text = re.sub(f'^{label}:', '', description_text, flags=re.MULTILINE | re.IGNORECASE).strip()

            info["Description"] = ' '.join(description_text.split())
            records.append(info)
            
        return records

    except Exception as e:
        return []

# --- Main UI Layout ---
st.title("💸 KCI Major Gift Scraper")
st.markdown("""
<div style='background-color: #000000; padding: 15px; border-radius: 10px; border: 2px solid #CC0633; color: white;'>
This tool scrapes <b>Major Gift News</b> from <i>KCI Philanthropy</i>. 
It uses a headless browser to paginate through the listing and extracts details into an Excel sheet.
</div>
<br>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Settings")
    max_clicks = st.number_input("Max 'View More' Clicks", min_value=1, max_value=50, value=2, help="Start small (1-3) to test memory limits.")
    delay = st.number_input("Request Delay (s)", min_value=0.1, value=0.5, step=0.1)
    st.info("The scraping process runs in the cloud. Please stay on this tab while it runs.")

if st.button("Start Scraping", type="primary"):
    status_area = st.empty()
    progress_bar = st.progress(0)
    driver = None
    
    try:
        # Phase 1: Selenium (High Memory Usage)
        status_area.info("Starting Browser... (This may take a moment)")
        driver = get_driver()
        
        urls = get_all_article_urls(driver, max_clicks, status_area)
        
    except Exception as e:
        st.error(f"Critical Browser Error: {e}")
        urls = []
    finally:
        if driver:
            driver.quit()
            del driver
            gc.collect() 
            status_area.text("Browser closed. Releasing memory...")

    # Phase 2: Requests (Low Memory Usage)
    if not urls:
        status_area.warning("No articles found or browser crashed before finding any.")
    else:
        status_area.success(f"Found {len(urls)} articles. Starting fast extraction...")
        
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 ..."})
        
        all_records = []
        
        data_container = st.empty()
        
        for i, url in enumerate(sorted(urls)):
            records = parse_single_article(url, session)
            all_records.extend(records)
            
            progress = (i + 1) / len(urls)
            progress_bar.progress(progress)
            status_area.text(f"Processing {i+1}/{len(urls)}: {url.split('/')[-2]}")
            time.sleep(delay)

        progress_bar.empty()
        
        if all_records:
            df = pd.DataFrame(all_records)
            cols = ["Donor", "Gift", "Recipient", "City", "Province", "Date", "Description", "Source URL"]
            df = df[[c for c in cols if c in df.columns]]
            
            status_area.success(f"✅ Scraping Complete! {len(all_records)} gifts found.")
            data_container.dataframe(df)
            
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Sheet1')
                
            st.download_button(
                label="📥 Download Excel File",
                data=buffer.getvalue(),
                file_name="major_gift_news.xlsx",
                mime="application/vnd.ms-excel"
            )
        else:
            status_area.warning("Scraping finished, but no gift records were successfully parsed.")
