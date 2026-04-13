from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.route('/')
def home():
    return "Server running"


@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    text = data.get("text")

    prompt = f"""
Analyze this emergency message:
"{text}"

Give output in this format:
Type:
Urgency:
Panic Level:
"""

    try:
        # ✅ TEMPORARY STATIC OUTPUT (no API errors)
        output = f"""
Type: Fire
Urgency: High
Panic Level: High
"""

        return jsonify({
            "output": output
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        })


if __name__ == "__main__":
    app.run(debug=True)