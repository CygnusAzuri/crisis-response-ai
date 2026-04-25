from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
import traceback
import os
from datetime import datetime

# ✅ NEW: Google AI (Gemini)
import google.generativeai as genai

app = Flask(__name__)
CORS(app)

# =========================
# KEYS
# =========================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")   # 👈 NEW

# Setup Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-pro")
    print("✅ Google Gemini API loaded.")
else:
    gemini_model = None
    print("⚠️ GEMINI_API_KEY not set.")

# Setup Groq
if not GROQ_API_KEY:
    print("⚠️ WARNING: GROQ_API_KEY is not set. Running in fallback-only mode.")
else:
    print("✅ GROQ_API_KEY loaded successfully.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

reports = []

# ══════════════════════════════════════════════
# NORMALIZATION
# ══════════════════════════════════════════════
def normalize_type(raw_text: str) -> str:
    text = raw_text.lower()

    keyword_map = [
        ("FIRE", ["fire", "burn", "smoke", "aag"]),
        ("MEDICAL", ["injury", "bleeding", "faint", "ambulance"]),
        ("SECURITY", ["attack", "robbery", "threat", "knife"]),
        ("NATURAL DISASTER", ["earthquake", "flood", "storm"]),
        ("ACCIDENT", ["accident", "crash", "fall"]),
    ]

    for category, keywords in keyword_map:
        for kw in keywords:
            if kw in text:
                return category

    return "OTHER"

def normalize_level(raw_text: str, field: str) -> str:
    text = raw_text.lower()

    for line in raw_text.splitlines():
        if field.lower() in line.lower():
            if "high" in line.lower(): return "HIGH"
            if "medium" in line.lower(): return "MEDIUM"
            if "low" in line.lower(): return "LOW"

    if "high" in text: return "HIGH"
    if "medium" in text: return "MEDIUM"
    if "low" in text: return "LOW"

    return "MEDIUM"

def save_report(text, output_text):
    report = {
        "type": normalize_type(output_text),
        "urgency": normalize_level(output_text, "urgency"),
        "panic": normalize_level(output_text, "panic"),
        "message": text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    reports.append(report)

# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════
@app.route('/')
def home():
    return "✅ CrisisSense Server Running"

@app.route('/reports', methods=['GET'])
def get_reports():
    return jsonify({"total": len(reports), "reports": reports})

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Please enter a message"})

    prompt = f"""
    Analyze this emergency:

    "{text}"

    Format:
    Type:
    Urgency:
    Panic Level:

    Suggested Action:
    1.
    2.
    3.
    """

    # =========================
    # 1️⃣ TRY GOOGLE GEMINI FIRST
    # =========================
    if gemini_model:
        try:
            print("🌐 Using Google Gemini...")
            response = gemini_model.generate_content(prompt)
            raw_output = response.text

            normalized_output = inject_normalized_type(
                raw_output,
                normalize_type(raw_output + text)
            )

            save_report(text, normalized_output)
            clean_output = normalized_output.replace("**", "")
            return jsonify({"output": clean_output})

        except Exception as e:
            print("❌ Gemini failed:", e)

    # =========================
    # 2️⃣ FALLBACK → GROQ
    # =========================
    if client:
        try:
            print("📡 Using Groq fallback...")

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}]
            )

            raw_output = response.choices[0].message.content

            normalized_output = inject_normalized_type(
                raw_output,
                normalize_type(raw_output + text)
            )

            save_report(text, normalized_output)
            clean_output = normalized_output.replace("**", "")
            return jsonify({"output": clean_output})
            

        except Exception as e:
            print("❌ Groq failed:", e)

    # =========================
    # 3️⃣ FINAL FALLBACK
    # =========================
    return _fallback(text)

# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
def inject_normalized_type(raw_output: str, normalized_type: str) -> str:
    lines = raw_output.splitlines()
    result = []

    for line in lines:
        if "Type:" in line:
            result.append(f"🚑 Emergency Type: {normalized_type}")
        else:
            result.append(line)

    return "\n".join(result)

def _fallback(text: str):
    type_ = normalize_type(text)

    output = f"""
🚑 Emergency Type: {type_}
⚡ Urgency: MEDIUM
😨 Panic Level: MEDIUM

👉 Suggested Action:
Stay calm and contact emergency services.
"""

    save_report(text, output)
    return jsonify({"output": output})

# ══════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True)