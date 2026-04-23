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

reports = []


# ══════════════════════════════════════════════
# ✅ NORMALIZATION LAYER
# Converts any AI free-text output → standard category
# Works by scanning the FULL output text for keywords
# ══════════════════════════════════════════════
def normalize_type(raw_text: str) -> str:
    text = raw_text.lower()

    keyword_map = [
        # Each tuple: (standard category, list of keywords to match)
        ("FIRE",             ["fire", "burn", "burning", "flame", "smoke", "aag", "blaze", "inferno"]),
        ("MEDICAL",          ["faint", "fainting", "unconscious", "bleed", "bleeding", "injury", "injured",
                              "medical", "ambulance", "heart", "chest pain", "seizure", "stroke",
                              "breathe", "breathing", "overdose", "poison", "fracture", "broken bone"]),
        ("SECURITY",         ["follow", "following", "stalk", "stalking", "threat", "threaten",
                              "attack", "attacked", "robbery", "robber", "steal", "theft", "thief",
                              "harass", "harassment", "assault", "danger", "dangerous", "weapon",
                              "gun", "knife", "murder", "abduct", "kidnap", "suspicious", "intruder",
                              "break in", "violence", "violent", "police", "crime"]),
        ("NATURAL DISASTER", ["earthquake", "flood", "flooding", "cyclone", "hurricane", "tornado",
                              "tsunami", "landslide", "drought", "storm", "lightning", "avalanche",
                              "wildfire", "volcano"]),
        ("ACCIDENT",         ["accident", "crash", "collision", "hit", "vehicle", "car crash",
                              "road accident", "fell", "fall", "slip", "electric shock", "electrocute"]),
    ]

    for category, keywords in keyword_map:
        for kw in keywords:
            if kw in text:
                return category

    return "OTHER"   # graceful fallback — never "UNKNOWN"


def normalize_level(raw_text: str, field: str) -> str:
    """
    Extracts HIGH / MEDIUM / LOW from AI output for urgency/panic.
    Falls back to keyword scanning if AI didn't follow format exactly.
    """
    text = raw_text.lower()

    # Try to find the field line first
    for line in raw_text.splitlines():
        if field.lower() in line.lower():
            line_lower = line.lower()
            if "high"   in line_lower: return "HIGH"
            if "medium" in line_lower: return "MEDIUM"
            if "low"    in line_lower: return "LOW"

    # Fallback: scan whole text
    if "high"   in text: return "HIGH"
    if "medium" in text: return "MEDIUM"
    if "low"    in text: return "LOW"

    return "MEDIUM"   # safe default


def save_report(text, output_text):
    type_    = normalize_type(output_text)
    urgency  = normalize_level(output_text, "urgency")
    panic    = normalize_level(output_text, "panic")

    report = {
        "type":      type_,
        "urgency":   urgency,
        "panic":     panic,
        "message":   text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    reports.append(report)
    print(f"📋 Report saved: {report}")


# ══════════════════════════════════════════════
# Routes
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

    if not client:
        return _fallback(text, reason="API key not configured")

    # ✅ UPDATED PROMPT — tells AI to be descriptive, we normalize ourselves
    prompt = f"""
    You are an emergency response AI. Analyze the following message:

    "{text}"

    Respond EXACTLY in this format:

    Type: [Fire / Medical / Security / Natural Disaster / Accident / Other]
    Urgency: [HIGH / MEDIUM / LOW]
    Panic Level: [HIGH / MEDIUM / LOW]

    Suggested Action:
    1. First immediate step
    2. Second step
    3. Third step
    4. Fourth step
    5. Fifth step

    Rules:
    - Steps must be specific to THIS situation
    - Keep them short and practical
    - Focus on safety until help arrives
    """

    try:
        print("📡 Calling Groq AI...")

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )

        raw_output = response.choices[0].message.content

        if not raw_output or raw_output.strip() == "":
            raise Exception("Groq returned an empty response")

        print("✅ AI response received.")
        print("🔍 Raw AI output:\n", raw_output)

        # ✅ NORMALIZE — replace whatever AI said for type with standard category
        normalized_type = normalize_type(raw_output + " " + text)  # scan both AI output AND original message
        print(f"🏷️  Normalized type: {normalized_type}")

        # Inject normalized type back into output so frontend gets clean data
        normalized_output = inject_normalized_type(raw_output, normalized_type)

        save_report(text, normalized_output)
        return jsonify({"output": normalized_output})

    except Exception as e:
        print("❌ AI call failed:")
        traceback.print_exc()
        return _fallback(text, reason=str(e))


def inject_normalized_type(raw_output: str, normalized_type: str) -> str:
    """
    Replaces the Emergency Type line in AI output with our normalized value.
    Keeps everything else (urgency, panic, action) exactly as AI wrote it.
    """
    lines = raw_output.splitlines()
    result = []
    for line in lines:
        if "Emergency Type:" in line:
            result.append(f"🚑 Emergency Type: {normalized_type}")
        else:
            result.append(line)
    return "\n".join(result)


def _fallback(text: str, reason: str = "Unknown error"):
    print(f"🔁 Using fallback. Reason: {reason}")

    # ✅ Use normalization on original message too
    type_ = normalize_type(text)

    action_map = {
        "FIRE":             "Evacuate immediately and call the fire department",
        "MEDICAL":          "Provide first aid and call an ambulance immediately",
        "SECURITY":         "Move to a safe place and call the police immediately",
        "NATURAL DISASTER": "Move to higher ground or a safe structure immediately",
        "ACCIDENT":         "Do not move the injured, call emergency services",
        "OTHER":            "Stay calm, assess the situation, and call emergency services",
    }

    text_lower = text.lower()
    urgency = "HIGH"   if any(w in text_lower for w in ["help","urgent","emergency","please","dying"]) else \
              "MEDIUM" if any(w in text_lower for w in ["need","want","someone"]) else "LOW"
    panic   = "HIGH"   if ("!" in text or any(w in text_lower for w in ["help","please","scared","afraid"])) else "MEDIUM"

    output = (
        f"🚑 Emergency Type: {type_}\n"
        f"⚡ Urgency: {urgency}\n"
        f"😨 Panic Level: {panic}\n"
        f"👉 Suggested Action:\n{action_map.get(type_, action_map['OTHER'])}"
    )

    save_report(text, output)
    return jsonify({"output": f"⚠️ FALLBACK (AI unavailable: {reason}):\n\n{output}"})


if __name__ == "__main__":
    app.run(debug=True)