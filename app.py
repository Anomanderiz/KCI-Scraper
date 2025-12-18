import streamlit as st
import os
import time
import requests
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
from io import BytesIO

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# --- Configuration & Setup ---
st.set_page_config(page_title="KCI Major Gift Scraper", page_icon="🎁", layout="wide")

def get_driver():
    """
    Initializes a headless Chrome browser optimized for containerized environments
    like Streamlit Cloud.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    # This user agent mimics a real user to avoid immediate detection
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    # Streamlit Cloud specific driver loading
    return webdriver.Chrome(options=chrome_options)

def get_all_article_urls(driver, max_clicks, status_container):
    """
    Navigates the KCI listing page and clicks 'View More' until the limit is reached
    or no more articles appear.
    """
    DOMAIN = "https://kciphilanthropy.com"
    LISTING_PATH = "/insights/"
    LISTING_PARAMS = "fwp_categories=major-gift-news"
    full_url = f"{DOMAIN}{LISTING_PATH}?{LISTING_PARAMS}"
    
    status_container.info(f"Navigating to {full_url}...")
    driver.get(full_url)
    
    all_urls = set()
    click_count = 0
    
    # Initial load
    status_container.text("Analyzing initial page load...")
    
    while click_count < max_clicks:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        current_urls = {urljoin(DOMAIN, a['href']) for a in soup.select("a[href*='/major-gift-news-']")}
        
        new_urls = current_urls - all_urls
        if not new_urls and click_count > 0:
            status_container.warning("No new URLs found. Stopping pagination.")
            break
        
        all_urls.update(new_urls)
        status_container.text(f"Collected {len(all_urls)} unique articles so far...")

        try:
            # Locate the "View More" button
            view_more_button = driver.find_element(By.CLASS_NAME, "fwp-load-more")
            
            # Check visibility
            if not view_more_button.is_displayed():
                break

            # Scroll and Click
            driver.execute_script("arguments[0].scrollIntoView(true);", view_more_button)
            time.sleep(0.5) 
            view_more_button.click()
            click_count += 1
            
            # Wait for the loading spinner to appear and then disappear
            wait = WebDriverWait(driver, 10)
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".facetwp-loading")))
            
        except (NoSuchElementException, TimeoutException):
            break
        except Exception as e:
            st.error(f"Pagination error: {e}")
            break
            
    return list(all_urls)

def parse_single_article(url, session):
    """
    Extracts donor info from a single article URL.
    """
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        
        records = []
        # Find the main article content
        main_content = soup.find("article") or soup.find("main") or soup

        # Logic: Find H2 tags (Donors) and parse the content between them
        for h2 in main_content.find_all('h2'):
            donor = h2.get_text(strip=True)
            # Filter out boilerplate headers
            if not donor or "submissions notice" in donor.lower():
                continue

            # Gather siblings until the next H2
            content_block = []
            for sibling in h2.find_next_siblings():
                if sibling.name == 'h2':
                    break
                content_block.append(sibling)
            
            if not content_block: continue

            # Extract details
            info = {"Donor": donor, "Gift": "", "Recipient": "", "City": "", "Province": "", "Date": "", "Description": "", "Source URL": url}
            block_soup = BeautifulSoup("".join(str(s) for s in content_block), "html.parser")
            block_text = block_soup.get_text(separator="\n", strip=True)

            # Extract fields labeled with H3 (e.g. Recipient: ...)
            for h3 in block_soup.find_all('h3'):
                label = h3.get_text(strip=True).replace(":", "")
                if label in info:
                    next_elem = h3.find_next_sibling()
                    if next_elem:
                        info[label] = next_elem.get_text(strip=True)

            # Extract Date from H6
            date_tag = block_soup.find('h6')
            if date_tag:
                info["Date"] = date_tag.get_text(strip=True)
            
            # Regex for money
            gift_pattern = re.compile(r'\$\d[\d,.]*\s*(million|billion|thousand)?', re.IGNORECASE)
            gift_match = gift_pattern.search(block_text)
            if gift_match:
                info["Gift"] = gift_match.group(0)

            # Clean up description
            description_text = block_text
            for key, value in info.items():
                if value and key != "Source URL":
                    description_text = description_text.replace(value, "")
            
            # Remove labels like "Recipient:" from the description text
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
    max_clicks = st.number_input("Max 'View More' Clicks", min_value=1, max_value=200, value=5, help="Increase this to scrape more historical data.")
    delay = st.number_input("Request Delay (s)", min_value=0.1, value=0.5, step=0.1)
    st.info("The scraping process runs in the cloud. Please stay on this tab while it runs.")

if st.button("Start Scraping", type="primary"):
    status_area = st.empty()
    progress_bar = st.progress(0)
    
    try:
        # 1. Initialize Browser
        driver = get_driver()
        
        # 2. Get URLs
        urls = get_all_article_urls(driver, max_clicks, status_area)
        driver.quit() # Close browser to save memory
        
        if not urls:
            status_area.error("No articles found.")
        else:
            status_area.success(f"Found {len(urls)} articles. Beginning detailed extraction...")
            
            # 3. Parse Articles
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0 ..."})
            
            all_records = []
            
            results_placeholder = st.empty()
            
            for i, url in enumerate(sorted(urls)):
                records = parse_single_article(url, session)
                all_records.extend(records)
                
                # Update progress
                progress = (i + 1) / len(urls)
                progress_bar.progress(progress)
                status_area.text(f"Processing {i+1}/{len(urls)}: {url.split('/')[-2]}")
                time.sleep(delay)
            
            # 4. Final Output
            progress_bar.empty()
            if all_records:
                df = pd.DataFrame(all_records)
                
                # Reorder columns nicely
                cols = ["Donor", "Gift", "Recipient", "City", "Province", "Date", "Description", "Source URL"]
                df = df[[c for c in cols if c in df.columns]]
                
                status_area.success(f"✅ Scraping Complete! {len(all_records)} gifts found.")
                st.dataframe(df)
                
                # Convert to Excel for download
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

    except Exception as e:
        st.error(f"A critical error occurred: {e}")
