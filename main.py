import os
from fastapi import FastAPI, Header, HTTPException
import firebase_admin
from firebase_admin import credentials, auth, firestore
from pydantic import BaseModel
from datetime import datetime
import pytz
from openai import OpenAI
from dotenv import load_dotenv

# --- 1. SETUP & KONFIGURASI ---

# Load API Key dari file .env
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Inisialisasi OpenAI Client
client = OpenAI(api_key=OPENAI_API_KEY)

# Inisialisasi Firebase (Cek agar tidak double init saat reload)
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

# Inisialisasi Database Firestore
db = firestore.client()

# Inisialisasi Aplikasi Server
app = FastAPI()

# Model data yang diterima dari Flutter
class ChatRequest(BaseModel):
    message: str

# --- 2. FUNGSI CEK LIMIT (KUOTA) ---
def check_limit_and_update(uid: str, max_limit: int = 10):
    # Set zona waktu Indonesia (WIB) agar reset jam 00:00 WIB
    tz = pytz.timezone('Asia/Jakarta')
    today_str = datetime.now(tz).strftime("%Y-%m-%d") # Contoh: "2023-10-27"

    # Referensi ke dokumen user di koleksi 'user_limits'
    doc_ref = db.collection('user_limits').document(uid)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        last_date = data.get('date')
        current_count = data.get('count', 0)

        # Jika tanggal sama dengan hari ini
        if last_date == today_str:
            if current_count >= max_limit:
                return False # FALSE: Kuota habis
            else:
                # Tambah count + 1
                doc_ref.update({'count': firestore.Increment(1)})
                return True # TRUE: Boleh lanjut
        else:
            # Jika tanggal beda (sudah ganti hari) -> Reset jadi 1
            doc_ref.set({'date': today_str, 'count': 1})
            return True
    else:
        # User baru pertama kali chat -> Buat data baru
        doc_ref.set({'date': today_str, 'count': 1})
        return True

# --- 3. FUNGSI REQUEST KE OPENAI ---
def get_openai_response(user_message: str):
    try:
        # --- PERUBAHAN DI SINI (System Prompt) ---
        system_instruction = """
        Kamu adalah Asisten Pintar khusus untuk aplikasi 'Smart Farming'. 
        Tugasmu hanya menjawab pertanyaan seputar pertanian, perkebunan, peternakan, cuaca, tanah, pupuk, dan teknologi pertanian (IoT).
        
        ATURAN PENTING:
        1. Jika user bertanya tentang topik pertanian, jawab dengan ramah, informatif, dan membantu.
        2. Jika user bertanya di luar topik pertanian (misal: resep masakan, coding, politik, film, matematika umum), tolak dengan sopan.
        3. Contoh penolakan: "Maaf, saya hanya bisa menjawab pertanyaan seputar pertanian."
        4. Gunakan Bahasa Indonesia yang baik dan mudah dimengerti petani.
        """
        # ------------------------------------------

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_instruction}, # Masukkan instruksi di sini
                {"role": "user", "content": user_message}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "Maaf, otak AI saya sedang gangguan."

# --- 4. ENDPOINT UTAMA (YANG DIPANGGIL FLUTTER) ---
@app.post("/chat")
async def chat_endpoint(request: ChatRequest, authorization: str = Header(None)):
    
    # A. Validasi Header Authorization
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Token missing")

    token = authorization.split("Bearer ")[1]

    try:
        # B. Verifikasi Token Firebase (Siapa user ini?)
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        
        # C. Cek Limit Harian User
        is_allowed = check_limit_and_update(uid, max_limit=5)
        
        if not is_allowed:
            # Kode 429 = Too Many Requests
            raise HTTPException(status_code=429, detail="Kuota harian habis.")

        # D. Kirim ke OpenAI (Hanya jika limit aman)
        bot_reply = get_openai_response(request.message)

        return {"response": bot_reply}

    except HTTPException as he:
        raise he # Lempar error HTTP (401 atau 429)
    except Exception as e:
        print(f"Server Error: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")