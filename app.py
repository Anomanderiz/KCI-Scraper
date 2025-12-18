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
st.set_page_config(page_title="KCI Major Gift Scraper", page_icon="🎁", layout="wide")

def get_driver():
    """
    Initializes a headless Chrome browser with AGGRESSIVE memory saving options.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") # Newer, slightly more efficient headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor") # Disable visual compositor to save RAM
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
    
    # Fail-safe: Stop if we hit a hard limit to prevent infinite loops/crashes
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
            time.sleep(1.0) # Increased sleep slightly to be gentler on CPU
            view_more_button.click()
            click_count += 1
            
            wait = WebDriverWait(driver, 10)
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".facetwp-loading")))
            
        except (NoSuchElementException, TimeoutException):
            break
        except Exception as e:
            # Don't crash the whole app for one pagination error
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
This tool scrapes **Major Gift News** from *KCI Philanthropy*. 
It uses a headless browser to paginate through the listing and extracts details into an Excel sheet.
""")

with st.sidebar:
    st.header("Settings")
    # Decreased default max clicks to prevent initial OOM crash
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
        # CRITICAL: Kill the browser immediately to free up RAM for parsing
        if driver:
            driver.quit()
            del driver
            gc.collect() # Force garbage collection
            status_area.text("Browser closed. Releasing memory...")

    # Phase 2: Requests (Low Memory Usage)
    if not urls:
        status_area.warning("No articles found or browser crashed before finding any.")
    else:
        status_area.success(f"Found {len(urls)} articles. Starting fast extraction...")
        
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 ..."})
        
        all_records = []
        
        # Create a container for the dataframe so we can update it live
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
