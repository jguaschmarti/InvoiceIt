import streamlit as st
import streamlit_authenticator as stauth

import base64
import requests
from pdf2image import convert_from_bytes
from io import BytesIO
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

st.set_page_config(layout="wide")

api_key = st.secrets["api"]["api_key"]
cookie_key = st.secrets["auth"]["cookie_key"]
google_credentials = {
    "type": st.secrets["google_credentials"]["type"],
    "project_id": st.secrets["google_credentials"]["project_id"],
    "private_key_id": st.secrets["google_credentials"]["private_key_id"],
    "private_key": st.secrets["google_credentials"]["private_key"],
    "client_email": st.secrets["google_credentials"]["client_email"],
    "client_id": st.secrets["google_credentials"]["client_id"],
    "auth_uri": st.secrets["google_credentials"]["auth_uri"],
    "token_uri": st.secrets["google_credentials"]["token_uri"],
    "auth_provider_x509_cert_url": st.secrets["google_credentials"]["auth_provider_x509_cert_url"],
    "client_x509_cert_url": st.secrets["google_credentials"]["client_x509_cert_url"]
}

# Authentication setup
# Sample credentials (in production, use secrets management)
credentials = {
    "usernames": {
        "user1": {
            "name": "Jaume",
            "password": st.secrets["auth"]["user1_password"]  # Hashed password
        },
        "user2": {
            "name": "Olga",
            "password": st.secrets["auth"]["user2_password"]  # Hashed password
        }
    }
}

# Create an authenticator instance
authenticator = stauth.Authenticate(
    credentials, 
    cookie_name="invoice_app_cookie", 
    key=cookie_key, 
    cookie_expiry_days=30
)


# Google Sheets Setup
def connect_to_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(google_credentials, scope)
    client = gspread.authorize(creds)
    return client

# Function to append data to Google Sheets
def append_to_gsheet(sheet, data):
    sheet.append_row(data)

# Function to fetch all data from Google Sheets
def fetch_gsheet_data(sheet):
    data = sheet.get_all_records()
    if not data:
        return pd.DataFrame(columns=['code', 'item_name', 'price_per_unit', 'quantity', 'discount', 'date'])
    return pd.DataFrame(data)

# Convert PDF to base64 images
def convert_pdf_to_base64(pdf_file, dpi=300):
    pdf_file.seek(0)
    pdf = pdf_file.read()
    images = convert_from_bytes(pdf, dpi, first_page=1, last_page=1)
    base64_images = []
    for image in images:
        buffered = BytesIO()
        image.save(buffered, format="JPEG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        base64_images.append(img_base64)
    return base64_images

# Function to check if the sheet is empty and initialize headers
def initialize_sheet_with_headers(sheet, headers):
    rows = sheet.get_all_values()
    if len(rows) == 0 or len(rows) == 1:
        sheet.append_row(headers)

# Function to append extracted data to both sheets
def append_extracted_data_to_gsheet(insert_sheet, extracted_items):
    headers = ['code', 'item_name', 'price_per_unit', 'quantity', 'discount', 'date']
    initialize_sheet_with_headers(insert_sheet, headers)
    for item in extracted_items:
        row = [
            item['code'],
            item['item_name'],
            item['price_per_unit'],
            item['quantity'],
            item['discount'],
            item['date']
        ]
        append_to_gsheet(insert_sheet, row)

def send_image_to_api_and_store(api_key, image_base64, insert_sheet):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the code, item name, price per unit, quantity and discount of the items in the invoice, return it in a list of dicts format, price should have two decimals. Also include the date of the invoice in each product in format dd/mm/yyyy. If there is no discount, put 0. If there are rows without quantity or without code, aggregate the price with the first item above with code and quantity. Only return the list, do not return any other text."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                            "detail": "high"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 300
    }

    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    extracted_items = response.json()['choices'][0]['message']['content']
    print(extracted_items)
    extracted_items = eval(extracted_items.strip("```json"))
    append_extracted_data_to_gsheet(insert_sheet, extracted_items)

# Function to process and update product_sheet (kept unchanged from the original)
def process_and_update_product_sheet(insert_sheet, product_sheet):
    insert_data = insert_sheet.get_all_records()
    if not insert_data:
        st.write("No data found in insert_sheet.")
        return

    df = pd.DataFrame(insert_data)
    df['code'] = df['code'].astype(str)
    df['item_name'] = df['item_name'].astype(str)
    df['price_per_unit'] = pd.to_numeric(df['price_per_unit'], errors='coerce')
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
    df['discount'] = pd.to_numeric(df['discount'], errors='coerce')
    df['date'] = pd.to_datetime(df['date'], errors='coerce', dayfirst=True)
    invalid_rows = df[df[['code', 'item_name', 'price_per_unit', 'quantity', 'discount', 'date']].isnull().any(axis=1)]
    if not invalid_rows.empty:
        st.write(f"Dropping {len(invalid_rows)} rows with invalid data:")
        st.write(invalid_rows)
    df.dropna(subset=['code', 'item_name', 'price_per_unit', 'quantity', 'discount', 'date'], inplace=True)
    df_sorted = df.sort_values('price_per_unit', ascending=False)
    grouped = df_sorted.groupby(['code', 'item_name', 'date'], as_index=False).agg({
        'price_per_unit': 'first',
        'quantity': 'first',
        'discount': 'first'
    })
    newest_data = grouped.sort_values('date').groupby(['code', 'item_name'], as_index=False).last()
    newest_data['date'] = newest_data['date'].dt.strftime('%d/%m/%Y')
    product_sheet.clear()
    rows = newest_data.values.tolist()
    headers = newest_data.columns.tolist()
    product_sheet.append_row(headers)
    for row in rows:
        product_sheet.append_row(row)
    st.write("Product sheet updated successfully with the latest data.")

# Pages for file upload and data visualization (unchanged)
def file_upload_page(api_key, insert_sheet, product_sheet):
    st.title("File Upload and Invoice Parsing")
    
    # File uploader: allow multiple PDF uploads
    uploaded_files = st.file_uploader("Choose PDF files", accept_multiple_files=True, type=["pdf"])
    
    if uploaded_files:
        # Display the total number of files to process
        total_files = len(uploaded_files)
        st.write(f"Uploaded {total_files} files.")
        
        # Initialize a progress bar for overall file processing
        progress_bar = st.progress(0)  # Starting progress
        total_steps = sum([len(convert_pdf_to_base64(f)) for f in uploaded_files])  # Total steps for all files
        step = 0  # Current progress step
        
        for uploaded_file in uploaded_files:
            st.write(f"Processing file: {uploaded_file.name}")

            # Convert PDF to base64 images
            base64_images = convert_pdf_to_base64(uploaded_file)
            
            for idx, image_base64 in enumerate(base64_images):
                with st.spinner(f"Sending page {idx + 1} of {uploaded_file.name} to API..."):
                    send_image_to_api_and_store(api_key, image_base64, insert_sheet)
                
                # Update the progress bar
                step += 1
                progress_bar.progress(step / total_steps)
        
        # Final message after processing all files
        st.success("All files have been processed and data added to the sheet.")
        process_and_update_product_sheet(insert_sheet, product_sheet)

def data_visualization_page(product_sheet):
    st.title("Data Visualization Page")
    product_data = product_sheet.get_all_records()
    if product_data:
        df = pd.DataFrame(product_data)
        df['code'] = df['code'].astype(str)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce', dayfirst=True).dt.strftime('%d/%m/%Y')
        st.dataframe(df)
    else:
        st.write("No data available in product_sheet.")

# Main function with authentication logic
def main():    
    # Add authentication logic
    name, authentication_status, username = authenticator.login("Login", "main")

    if authentication_status:
        st.sidebar.title(f"Welcome {name}!")
        st.sidebar.title("Navigation")
        page = st.sidebar.radio("Go to", ["Upload Files", "Visualize Data"])

        # Connect to Google Sheets
        client = connect_to_google_sheets()
        insert_sheet = client.open('InvoiceIt').worksheet('Sheet1')
        product_sheet = client.open('InvoiceIt').worksheet('Sheet2')

        if page == "Upload Files":
            file_upload_page(api_key, insert_sheet, product_sheet)
        elif page == "Visualize Data":
            data_visualization_page(product_sheet)

    elif authentication_status == False:
        st.error("Username/password is incorrect")
    elif authentication_status == None:
        st.warning("Please enter your username and password")

if __name__ == "__main__":
    main()