from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
import traceback
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

GROQ_API_KEY = os.getenv("GROQ_API_KEY") 

if not GROQ_API_KEY:
    print("⚠️  WARNING: GROQ_API_KEY is not set. Running in fallback-only mode.")
else:
    print("✅ GROQ_API_KEY loaded successfully.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ✅ NEW — in-memory report store
reports = []


def save_report(text, output_text):
    """Parse AI output and save structured report."""
    type_ = "UNKNOWN"
    urgency = "UNKNOWN"
    panic = "UNKNOWN"

    for line in output_text.splitlines():
        if "Emergency Type:" in line:
            type_ = line.split("Emergency Type:")[-1].strip()
        elif "Urgency:" in line:
            urgency = line.split("Urgency:")[-1].strip()
        elif "Panic Level:" in line:
            panic = line.split("Panic Level:")[-1].strip()

    report = {
        "type": type_,
        "urgency": urgency,
        "panic": panic,
        "message": text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    reports.append(report)
    print(f"📋 Report saved: {report}")


@app.route('/')
def home():
    return "✅ CrisisSense Server Running"


# ✅ NEW — reports endpoint
@app.route('/reports', methods=['GET'])
def get_reports():
    return jsonify({"total": len(reports), "reports": reports})


@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Please enter a message"})

    if not client:
        return _fallback(text, reason="API key not configured")

    prompt = f"""
Analyze this emergency message:
"{text}"

Respond in EXACTLY this format, nothing else:
🚑 Emergency Type: [TYPE IN CAPS]
⚡ Urgency: [HIGH/MEDIUM/LOW IN CAPS]
😨 Panic Level: [HIGH/MEDIUM/LOW IN CAPS]
👉 Suggested Action:
[One clear, practical action to take immediately]
"""

    try:
        print("📡 Calling Groq AI...")

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )

        output_text = response.choices[0].message.content

        if not output_text or output_text.strip() == "":
            raise Exception("Groq returned an empty response")

        print("✅ AI response received.")
        save_report(text, output_text)  # ✅ Save report after success
        return jsonify({"output": output_text})

    except Exception as e:
        print("❌ AI call failed:")
        traceback.print_exc()
        return _fallback(text, reason=str(e))


def _fallback(text: str, reason: str = "Unknown error"):
    print(f"🔁 Using fallback. Reason: {reason}")
    text_lower = text.lower()

    if "fire" in text_lower or "aag" in text_lower or "burn" in text_lower:
        type_ = "FIRE"
        action = "Evacuate immediately and call the fire department"
    elif "faint" in text_lower or "unconscious" in text_lower or "bleeding" in text_lower:
        type_ = "MEDICAL"
        action = "Provide first aid and call an ambulance immediately"
    elif "fight" in text_lower or "attack" in text_lower or "robbery" in text_lower:
        type_ = "SECURITY"
        action = "Move to safety and call the police immediately"
    else:
        type_ = "UNKNOWN"
        action = "Stay calm, assess the situation and call emergency services"

    urgency = "HIGH" if any(w in text_lower for w in ["help", "urgent", "emergency"]) else \
              "MEDIUM" if any(w in text_lower for w in ["please", "need"]) else "LOW"

    panic = "HIGH" if ("!" in text or "help" in text_lower or "please" in text_lower) else "MEDIUM"

    output = f"🚑 Emergency Type: {type_}\n⚡ Urgency: {urgency}\n😨 Panic Level: {panic}\n👉 Suggested Action:\n{action}"
    save_report(text, output)  # ✅ Save fallback report too
    return jsonify({"output": f"⚠️ FALLBACK (AI unavailable):\n\n{output}"})


if __name__ == "__main__":
    app.run(debug=True)